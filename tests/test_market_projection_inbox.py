"""Durable offline market projection outbox/inbox adversarial regressions."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.mode import QuantMode
from infrastructure.market_data_integrity import (
    MarketEventError, OfflineMarketIntegrityJournal,
)
from infrastructure.market_delivery_cursor import (
    DeliveryConflict, OfflineMarketProjectionCursor,
)
from infrastructure.market_projection_inbox import OfflineMarketProjectionInbox

BASE = 1_780_000_000_000


def trade(n):
    return {
        "e": "aggTrade", "s": "BTCUSDT", "a": n,
        "E": BASE, "T": BASE - 10, "f": n, "l": n,
        "p": str(n), "q": "0.01", "m": False,
    }


def make(tmp_path, *, project_count=2):
    mode = [QuantMode.SIMULATION]
    journal = OfflineMarketIntegrityJournal(
        tmp_path / "market.db", mode_provider=lambda: mode[0],
        market="binance_spot",
    )
    cursor = OfflineMarketProjectionCursor(journal)
    inbox = OfflineMarketProjectionInbox(cursor)
    for index in range(project_count):
        accepted = journal.ingest_agg_trade(
            trade(100 + index), source="research",
            received_at_ms=BASE + 100 + 10 * index,
        )
        assert accepted.accepted
        ticket = cursor.claim_next("indicator", now_ms=BASE + 200 + index)
        assert ticket is not None
        cursor.commit_projection(
            ticket, {"price": str(100 + index)},
            now_ms=BASE + 201 + index,
        )
    return inbox, cursor, journal, mode


def claim(inbox, receiver="risk", *, now=BASE + 300, **kwargs):
    return inbox.claim_next("indicator", receiver, now_ms=now, **kwargs)


def ack(inbox, ticket, value=None, *, now=BASE + 301):
    if value is None:
        value = {"verified_price": ticket.projection.value["price"]}
    return inbox.acknowledge(ticket, value, now_ms=now)


def test_projection_itself_is_durable_outbox_without_staging(tmp_path):
    inbox, cursor, _, _ = make(tmp_path)
    assert len(cursor.projections("indicator")) == 2
    assert inbox.receipts("indicator", "risk") == []
    a = claim(inbox)
    assert a.projection.value == {"price": "100"}
    assert inbox.receipts("indicator", "risk") == []
    receipt = ack(inbox, a)
    assert receipt.result == {"verified_price": "100"}
    assert receipt.source_available_at_ms == BASE + 100
    b = claim(inbox)
    assert b.ingest_id > a.ingest_id
    ack(inbox, b)
    assert claim(inbox) is None
    assert [r.result["verified_price"] for r in inbox.receipts("indicator", "risk")] == [
        "100", "101",
    ]


def test_crash_before_local_ack_reclaims_after_restart(tmp_path):
    inbox, _, journal, mode = make(tmp_path)
    before = claim(inbox, lease_ms=10)
    assert before is not None
    restarted_journal = OfflineMarketIntegrityJournal(
        journal.path, mode_provider=lambda: mode[0],
    )
    restarted = OfflineMarketProjectionInbox(
        OfflineMarketProjectionCursor(restarted_journal)
    )
    assert claim(restarted, now=BASE + 309) is None
    new = claim(restarted, now=BASE + 311, lease_ms=30)
    assert new is not None
    assert new.token != before.token
    assert new.ingest_id == before.ingest_id
    with pytest.raises(DeliveryConflict, match="superseded"):
        ack(inbox, before, now=BASE + 312)
    assert len(restarted.receipts("indicator", "risk")) == 0
    ack(restarted, new, now=BASE + 312)
    assert len(inbox.receipts("indicator", "risk")) == 1


def test_ack_is_idempotent_on_same_token_same_bytes_after_restart(tmp_path):
    inbox, _, journal, mode = make(tmp_path)
    ticket = claim(inbox)
    receipt = ack(inbox, ticket)
    reopened = OfflineMarketProjectionInbox(OfflineMarketProjectionCursor(
        OfflineMarketIntegrityJournal(journal.path, mode_provider=lambda: mode[0])
    ))
    assert reopened.acknowledge(
        ticket, {"verified_price": "100"}, now_ms=BASE + 900,
    ) == receipt
    with pytest.raises(DeliveryConflict, match="conflicting duplicate"):
        reopened.acknowledge(
            ticket, {"verified_price": "FORGED"}, now_ms=BASE + 900,
        )
    assert len(reopened.receipts("indicator", "risk")) == 1


def test_expired_lease_cannot_ack_without_reclaim(tmp_path):
    inbox, _, _, _ = make(tmp_path)
    first = claim(inbox, lease_ms=10)
    with pytest.raises(DeliveryConflict, match="expired"):
        ack(inbox, first, now=BASE + 310)
    assert inbox.receipts("indicator", "risk") == []
    newer = claim(inbox, now=BASE + 311)
    assert newer.token != first.token
    ack(inbox, newer, now=BASE + 312)


def test_independent_receivers_replay_same_projection_once_each(tmp_path):
    inbox, _, _, _ = make(tmp_path)
    a = claim(inbox, receiver="risk")
    b = claim(inbox, receiver="signals")
    assert a.ingest_id == b.ingest_id
    ack(inbox, a)
    assert inbox.receipts("indicator", "risk")
    assert not inbox.receipts("indicator", "signals")
    ack(inbox, b)
    assert len(inbox.receipts("indicator", "signals")) == 1
    assert claim(inbox, receiver="signals").projection.value["price"] == "101"


def test_concurrent_receiver_claims_issue_exactly_one_ticket(tmp_path):
    inbox, _, _, _ = make(tmp_path)
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: claim(inbox), range(32)))
    chosen = [r for r in results if r is not None]
    assert len(chosen) == 1
    ack(inbox, chosen[0])
    assert len(inbox.receipts("indicator", "risk")) == 1


def test_asof_does_not_skip_first_unobservable_projection(tmp_path):
    inbox, _, journal, _ = make(tmp_path, project_count=0)
    # Persist first event with a later receipt than the next one.
    for idx, arrival in enumerate((BASE + 250, BASE + 150)):
        assert journal.ingest_agg_trade(
            trade(100 + idx), source="research",
            received_at_ms=arrival,
        ).accepted
    producer = inbox.producer
    for k in range(2):
        t = producer.claim_next("indicator", now_ms=BASE + 350 + k)
        assert t is not None
        producer.commit_projection(
            t, {"price": str(100 + k)}, now_ms=BASE + 351 + k,
        )
    assert claim(inbox, now=BASE + 200, as_of_ms=BASE + 200) is None
    first = claim(inbox, now=BASE + 400, as_of_ms=BASE + 400)
    assert first.projection.value["price"] == "100"
    ack(inbox, first, now=BASE + 401)
    second = claim(inbox, now=BASE + 402, as_of_ms=BASE + 200)
    assert second.projection.value["price"] == "101"
    ack(inbox, second, now=BASE + 403)
    assert inbox.receipts("indicator", "risk", as_of_ms=BASE + 400) == []
    assert len(inbox.receipts("indicator", "risk", as_of_ms=BASE + 403)) == 2


def test_atomic_ack_trigger_failure_rolls_back_and_retry_succeeds(tmp_path):
    inbox, _, journal, _ = make(tmp_path)
    t = claim(inbox)
    with sqlite3.connect(journal.path) as conn:
        conn.execute("""
            CREATE TRIGGER reject_inbox_ack BEFORE DELETE
            ON offline_market_projection_inbox_claims
            BEGIN SELECT RAISE(ABORT,'injected inbox ACK failure'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="injected inbox ACK failure"):
        ack(inbox, t)
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_market_projection_inbox_receipts"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_market_projection_inbox_claims"
        ).fetchone()[0] == 1
        conn.execute("DROP TRIGGER reject_inbox_ack")
    ack(inbox, t)
    assert len(inbox.receipts("indicator", "risk")) == 1


def test_corrupt_projection_refused_before_new_inbox_claim(tmp_path):
    inbox, _, journal, _ = make(tmp_path)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE offline_market_projections SET value_json=? WHERE ingest_id=1",
            ('{"price":"FORGED"}',),
        )
    with pytest.raises(MarketEventError):
        claim(inbox)
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_market_projection_inbox_claims"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("column,payload", [
    ("result_json", '{"verified_price":"999"}'),
    ("source_available_at_ms", BASE + 999),
    ("projection_fingerprint", "0" * 64),
])
def test_corrupt_stored_receipt_is_detected(tmp_path, column, payload):
    inbox, _, journal, _ = make(tmp_path)
    ack(inbox, claim(inbox))
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            f"UPDATE offline_market_projection_inbox_receipts SET {column}=?",
            (payload,),
        )
    with pytest.raises(MarketEventError):
        inbox.receipts("indicator", "risk")


def test_reality_transition_denies_every_read_and_write(tmp_path):
    inbox, _, _, mode = make(tmp_path)
    t = claim(inbox)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        claim(inbox)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        ack(inbox, t)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        inbox.receipts("indicator", "risk")
    mode[0] = QuantMode.SIMULATION
    ack(inbox, t)


def test_bad_ids_lease_payload_and_timestamps_fail_closed(tmp_path):
    inbox, _, _, _ = make(tmp_path)
    for name in ("", "../escape", "space name"):
        with pytest.raises(ValueError):
            claim(inbox, receiver=name)
    for bad in (True, 0, 300001):
        with pytest.raises(ValueError):
            claim(inbox, lease_ms=bad)
    with pytest.raises(ValueError):
        claim(inbox, now=BASE + 300, as_of_ms=BASE + 301)
    with pytest.raises(ValueError):
        claim(inbox, now=True)
    t = claim(inbox)
    with pytest.raises(ValueError, match="finite JSON"):
        ack(inbox, t, {"value": float("nan")})
    with pytest.raises(ValueError, match="mapping"):
        ack(inbox, t, [1, 2])
    assert inbox.receipts("indicator", "risk") == []
    ack(inbox, t)
