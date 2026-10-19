"""Offline admission and reconciliation: no fake broker can trigger submission."""
from __future__ import annotations

import concurrent.futures
import sqlite3

import pytest

from quant.offline_admission import (
    BrokerObservation,
    IntentConflict,
    OfflineAdmissionLedger,
    ReconciliationError,
    fingerprint,
    stable_client_order_id,
)


def sample_intent():
    return {
        "symbol": "BTCUSDT", "side": "BUY",
        "quantity": 0.025, "reduce_only": False,
        "decision_id": "frozen-signal-001",
    }


def test_stable_client_id_is_derived_from_event_identity_not_wall_clock():
    a = stable_client_order_id("decision-1:BTCUSDT:leg-0")
    assert a == stable_client_order_id("decision-1:BTCUSDT:leg-0")
    assert len(a) <= 36
    assert a != stable_client_order_id("decision-2:BTCUSDT:leg-0")
    for invalid in ("", "with spaces", "../bad", "a" * 129):
        with pytest.raises(ValueError):
            stable_client_order_id(invalid)


def test_payload_hash_is_canonical_and_rejects_nan():
    first = sample_intent()
    reverse = dict(reversed(list(first.items())))
    assert fingerprint(first) == fingerprint(reverse)
    with pytest.raises(ValueError):
        fingerprint({"quantity": float("nan")})


def test_replayed_intent_is_admitted_exactly_once_and_remains_durable(tmp_path):
    path = tmp_path / "ledger.sqlite"
    first = OfflineAdmissionLedger(path).admit("intent-001", sample_intent())
    assert first.created is True and first.status == "ADMITTED"
    again = OfflineAdmissionLedger(path).admit("intent-001", sample_intent())
    assert again.created is False and again.client_order_id == first.client_order_id
    assert first.payload_fingerprint == again.payload_fingerprint
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM offline_intent_admission").fetchone()[0] == 1


def test_conflicting_reuse_of_stable_id_fails_closed(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    ledger.admit("immutable-001", sample_intent())
    altered = sample_intent()
    altered["quantity"] = 1000.0
    with pytest.raises(IntentConflict):
        ledger.admit("immutable-001", altered)
    assert ledger.get("immutable-001").status == "ADMITTED"


def test_concurrent_admission_across_connections_has_one_winner(tmp_path):
    location = tmp_path / "concurrent.sqlite"
    OfflineAdmissionLedger(location)

    def admit(_i):
        return OfflineAdmissionLedger(location).admit("once", sample_intent())

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(admit, range(18)))
    assert sum(entry.created for entry in results) == 1
    assert {entry.client_order_id for entry in results} == {
        stable_client_order_id("once")
    }
    with sqlite3.connect(location) as conn:
        assert conn.execute("SELECT COUNT(*) FROM offline_intent_admission").fetchone()[0] == 1


def test_atomic_dispatch_claim_only_once_across_replays(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    ledger.admit("id-a", sample_intent())
    assert ledger.begin_submission("id-a") is True
    assert ledger.begin_submission("id-a") is False
    assert ledger.get("id-a").status == "SUBMITTING"


def test_crash_restart_quarantines_pending_without_automatic_resubmit(tmp_path):
    path = tmp_path / "durable.sqlite"
    ledger = OfflineAdmissionLedger(path)
    ledger.admit("id-crash", sample_intent())
    assert ledger.begin_submission("id-crash")
    restarted = OfflineAdmissionLedger(path)
    assert restarted.quarantine_interrupted() == 1
    assert restarted.quarantine_interrupted() == 0
    assert restarted.get("id-crash").status == "UNKNOWN"
    assert restarted.begin_submission("id-crash") is False
    assert [item.intent_id for item in restarted.unresolved()] == ["id-crash"]


def test_not_found_is_ambiguous_and_does_not_allow_retry(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "durable.sqlite")
    entry = ledger.admit("maybe-sent", sample_intent())
    ledger.begin_submission("maybe-sent")
    calls = []
    def lookup(client_id):
        calls.append(client_id)
        return BrokerObservation(client_order_id=client_id, status="NOT_FOUND")
    state = ledger.reconcile_read_only("maybe-sent", lookup)
    assert calls == [entry.client_order_id]
    assert state.status == "UNKNOWN"
    assert ledger.begin_submission("maybe-sent") is False


def test_broker_open_partial_and_filled_are_read_only_evidence(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    original = ledger.admit("intent-002", sample_intent())
    ledger.begin_submission("intent-002")
    def partial(client_id):
        assert client_id == original.client_order_id
        return BrokerObservation(
            client_order_id=client_id, broker_order_id="EXCHANGE-MOCK-42",
            status="PARTIALLY_FILLED", filled_quantity=0.01,
        )
    got = ledger.reconcile_read_only("intent-002", partial)
    assert got.status == "ACKNOWLEDGED"
    assert got.filled_quantity == 0.01

    def completed(client_id):
        return BrokerObservation(
            client_order_id=client_id, broker_order_id="EXCHANGE-MOCK-42",
            status="FILLED", filled_quantity=0.025,
        )
    assert ledger.reconcile_read_only("intent-002", completed).status == "RECONCILED"
    assert ledger.begin_submission("intent-002") is False
    # Terminal rows are immutable even when a later read-only lookup is wrong.
    assert ledger.reconcile_read_only(
        "intent-002", lambda _cid: (_ for _ in ()).throw(AssertionError("no call"))
    ).status == "RECONCILED"


def test_lost_ack_and_mismatched_broker_identity_remain_unknown(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    ledger.admit("lost-ack", sample_intent())
    ledger.begin_submission("lost-ack")

    def down(_cid):
        raise TimeoutError("mock transport offline")
    with pytest.raises(ReconciliationError, match="held UNKNOWN"):
        ledger.reconcile_read_only("lost-ack", down)
    assert ledger.get("lost-ack").status == "UNKNOWN"
    with pytest.raises(ReconciliationError, match="mismatch"):
        ledger.reconcile_read_only(
            "lost-ack",
            lambda _: BrokerObservation(
                client_order_id="SPOOFED", broker_order_id="mock", status="FILLED"
            )
        )
    assert ledger.get("lost-ack").status == "UNKNOWN"


def test_conflicting_broker_order_reference_quarantined(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    a = ledger.admit("id-ref", sample_intent())
    ledger.begin_submission("id-ref")
    ledger.reconcile_read_only(
        "id-ref", lambda _: BrokerObservation(
            client_order_id=a.client_order_id, broker_order_id="mock-A",
            status="OPEN",
        )
    )
    with pytest.raises(ReconciliationError, match="conflicting broker order ID"):
        ledger.reconcile_read_only(
            "id-ref", lambda _: BrokerObservation(
                client_order_id=a.client_order_id, broker_order_id="mock-B",
                status="FILLED", filled_quantity=0.025,
            )
        )
    assert ledger.get("id-ref").status == "UNKNOWN"


def test_missing_broker_id_cannot_attest_a_fill(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    admission = ledger.admit("id-no-evidence", sample_intent())
    ledger.begin_submission(admission.intent_id)
    got = ledger.reconcile_read_only(
        admission.intent_id,
        lambda client: BrokerObservation(
            client_order_id=client, status="FILLED", filled_quantity=0.025,
        ),
    )
    assert got.status == "UNKNOWN"


def test_no_reconciliation_before_submission(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    ledger.admit("not-submitted", sample_intent())
    with pytest.raises(ReconciliationError, match="unsubmitted"):
        ledger.reconcile_read_only(
            "not-submitted",
            lambda _: (_ for _ in ()).throw(AssertionError("should not query")),
        )


def test_broker_rejected_is_terminal_only_with_order_evidence(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    obj = ledger.admit("id-rejected", sample_intent())
    ledger.begin_submission(obj.intent_id)
    result = ledger.reconcile_read_only(
        obj.intent_id,
        lambda cid: BrokerObservation(
            client_order_id=cid, status="REJECTED", broker_order_id="mock-reject-1",
        ),
    )
    assert result.status == "REJECTED"
    assert ledger.begin_submission(obj.intent_id) is False


def test_filled_without_positive_quantity_is_not_broker_attestation(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    row = ledger.admit("no-filled-volume", sample_intent())
    ledger.begin_submission(row.intent_id)
    with pytest.raises(ReconciliationError, match="positive filled quantity"):
        ledger.reconcile_read_only(
            row.intent_id,
            lambda cid: BrokerObservation(
                client_order_id=cid, status="FILLED",
                broker_order_id="MOCK-ID", filled_quantity=0.0,
            ),
        )
    assert ledger.get(row.intent_id).status == "UNKNOWN"


def test_regressing_partial_fill_is_quarantined_not_replayed(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    item = ledger.admit("progressive-fill", sample_intent())
    ledger.begin_submission(item.intent_id)
    ledger.reconcile_read_only(
        item.intent_id,
        lambda cid: BrokerObservation(
            client_order_id=cid, status="PARTIALLY_FILLED",
            broker_order_id="MOCK-ID", filled_quantity=0.012,
        ),
    )
    with pytest.raises(ReconciliationError, match="regressed"):
        ledger.reconcile_read_only(
            item.intent_id,
            lambda cid: BrokerObservation(
                client_order_id=cid, status="PARTIALLY_FILLED",
                broker_order_id="MOCK-ID", filled_quantity=0.005,
            ),
        )
    assert ledger.get(item.intent_id).status == "UNKNOWN"
    assert ledger.begin_submission(item.intent_id) is False


def test_one_broker_order_cannot_be_credited_to_two_intents(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "ledger.sqlite")
    first = ledger.admit("intent-a", sample_intent())
    second = ledger.admit("intent-b", sample_intent())
    assert first.client_order_id != second.client_order_id
    assert ledger.begin_submission(first.intent_id)
    assert ledger.begin_submission(second.intent_id)
    def evidence(cid):
        return BrokerObservation(
            client_order_id=cid,
            broker_order_id="VENUE-ONE-ONLY",
            status="FILLED",
            filled_quantity=0.025,
        )
    assert ledger.reconcile_read_only(first.intent_id, evidence).status == "RECONCILED"
    with pytest.raises(ReconciliationError, match="different intent"):
        ledger.reconcile_read_only(second.intent_id, evidence)
    assert ledger.get(first.intent_id).status == "RECONCILED"
    assert ledger.get(second.intent_id).status == "UNKNOWN"
    assert ledger.begin_submission(second.intent_id) is False
