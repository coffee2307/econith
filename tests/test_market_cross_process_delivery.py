"""Cross-process independent receiver ACK and crash-window regression tests."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.mode import QuantMode
from infrastructure.market_data_integrity import OfflineMarketIntegrityJournal
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.market_projection_inbox import OfflineMarketProjectionInbox
from infrastructure.market_cross_process_delivery import (
    CrossProcessDeliveryError,
    OfflineCrossProcessProjectionSender,
    OfflineIndependentProjectionReceiver,
    ProjectionEnvelope,
    ReceiverOwnedReceipt,
)

BASE = 1_780_000_000_000


def trade(n):
    return {
        "e": "aggTrade", "s": "BTCUSDT", "a": n,
        "E": BASE, "T": BASE - 10, "f": n, "l": n,
        "p": str(n), "q": "0.01", "m": False,
    }


def setup(tmp_path, count=3, mode=None):
    flag = [QuantMode.SIMULATION] if mode is None else mode
    journal = OfflineMarketIntegrityJournal(
        tmp_path / "source.sqlite", market="binance_spot",
        mode_provider=lambda: flag[0],
    )
    projection = OfflineMarketProjectionCursor(journal)
    inbox = OfflineMarketProjectionInbox(projection)
    for i in range(count):
        assert journal.ingest_agg_trade(
            trade(100 + i), source="research",
            received_at_ms=BASE + 100 + i,
        ).accepted
        claim = projection.claim_next("indicator", now_ms=BASE+200+i)
        assert claim is not None
        projection.commit_projection(
            claim, {"price": str(100 + i)}, now_ms=BASE+210+i,
        )
    target = OfflineIndependentProjectionReceiver(
        tmp_path / "receiver.sqlite", mode_provider=lambda: flag[0],
    )
    sender = OfflineCrossProcessProjectionSender(inbox)
    assert journal.path.resolve() != target.path.resolve()
    return sender, inbox, target, flag


def send(sender, target, *, now=BASE+300, **kwargs):
    return sender.deliver_once(
        "indicator", "risk",
        receiver_accept=lambda envelope: target.accept(
            envelope, now_ms=now,
        ),
        now_ms=now, **kwargs,
    )


def test_separate_sqlite_receipt_then_source_ack_and_full_backlog(tmp_path):
    sender, source, target, _ = setup(tmp_path)
    assert len(source.receipts("indicator", "risk")) == 0
    assert len(target.receipts("indicator", "risk")) == 0
    receipts = [send(sender, target, now=BASE+300+i) for i in range(3)]
    assert all(isinstance(r, ReceiverOwnedReceipt) for r in receipts)
    assert send(sender, target, now=BASE+320) is None
    assert [r.ingest_id for r in target.receipts("indicator", "risk")] == [
        r.ingest_id for r in receipts
    ]
    assert [r.result for r in source.receipts("indicator", "risk")] == [
        {"receiver_receipt_id": r.receipt_id} for r in receipts
    ]
    with sqlite3.connect(target.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_independent_projection_receipts"
        ).fetchone()[0] == 3


def test_sender_crash_after_receiver_commit_then_retry_is_idempotent(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    with pytest.raises(CrossProcessDeliveryError, match="injected sender crash"):
        send(sender, target, lease_ms=10, fail_after_receiver_commit=True)
    receipt = target.receipts("indicator", "risk")[0]
    assert source.receipts("indicator", "risk") == []
    assert send(sender, target, now=BASE+309) is None
    retry = send(sender, target, now=BASE+311)
    assert retry == receipt
    assert len(target.receipts("indicator", "risk")) == 1
    assert len(source.receipts("indicator", "risk")) == 1


def test_receiver_disk_error_cannot_advance_source_ack(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    with sqlite3.connect(target.path) as db:
        db.execute("""
            CREATE TRIGGER deny_write BEFORE INSERT ON
              offline_independent_projection_receipts
            BEGIN SELECT RAISE(ABORT,'receiver full disk'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="receiver full disk"):
        send(sender, target, now=BASE+300, lease_ms=10)
    assert source.receipts("indicator", "risk") == []
    assert target.receipts("indicator", "risk") == []
    with sqlite3.connect(target.path) as db:
        db.execute("DROP TRIGGER deny_write")
    result = send(sender, target, now=BASE+311)
    assert result is not None
    assert len(source.receipts("indicator", "risk")) == 1


def test_sender_ack_failure_after_receiver_commit_can_replay(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    with sqlite3.connect(source.journal.path) as db:
        db.execute("""
            CREATE TRIGGER deny_ack BEFORE INSERT ON
              offline_market_projection_inbox_receipts
            BEGIN SELECT RAISE(ABORT,'source ACK full disk'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="source ACK full disk"):
        send(sender, target, now=BASE+300, lease_ms=10)
    assert len(target.receipts("indicator", "risk")) == 1
    assert source.receipts("indicator", "risk") == []
    with sqlite3.connect(source.journal.path) as db:
        db.execute("DROP TRIGGER deny_ack")
    assert send(sender, target, now=BASE+311)
    assert len(source.receipts("indicator", "risk")) == 1
    assert len(target.receipts("indicator", "risk")) == 1


def test_conflicting_duplicate_payload_rejected_without_modifying_receiver(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    claim = source.claim_next("indicator", "risk", now_ms=BASE+300)
    env = sender.envelope(claim)
    first = target.accept(env, now_ms=BASE+300)
    altered = '{"price":"FORGED"}'
    conflicting = ProjectionEnvelope(
        env.source_consumer, env.receiver, env.ingest_id,
        env.event_id, env.available_at_ms, altered,
        hashlib.sha256(altered.encode()).hexdigest(),
    )
    with pytest.raises(CrossProcessDeliveryError, match="conflicting"):
        target.accept(conflicting, now_ms=BASE+301)
    assert target.receipts("indicator", "risk")[0] == first


@pytest.mark.parametrize("bad", [
    lambda x: ProjectionEnvelope(x.source_consumer, x.receiver, x.ingest_id,
                                  x.event_id, x.available_at_ms,
                                  '{"price":"NaN"}', x.projection_fingerprint),
    lambda x: ProjectionEnvelope(x.source_consumer, x.receiver, x.ingest_id,
                                  x.event_id, x.available_at_ms,
                                  x.projection_json, "0" * 64),
    lambda x: ProjectionEnvelope(x.source_consumer, x.receiver, x.ingest_id,
                                  x.event_id, BASE+100000,
                                  x.projection_json, x.projection_fingerprint),
    lambda x: ProjectionEnvelope(x.source_consumer, x.receiver, True,
                                  x.event_id, x.available_at_ms,
                                  x.projection_json, x.projection_fingerprint),
])
def test_bad_envelope_does_not_create_durable_receipt(tmp_path, bad):
    sender, source, target, _ = setup(tmp_path, count=1)
    claim = source.claim_next("indicator", "risk", now_ms=BASE+300)
    with pytest.raises((CrossProcessDeliveryError, ValueError)):
        target.accept(bad(sender.envelope(claim)), now_ms=BASE+300)
    assert target.receipts("indicator", "risk") == []


def test_forged_receiver_receipt_not_acknowledged_on_sender(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    def forged(env):
        return ReceiverOwnedReceipt(env.source_consumer, env.receiver,
                                    env.ingest_id, env.event_id,
                                    BASE+300, env.projection_fingerprint,
                                    "NOT_A_VALID_RECEIPT")
    with pytest.raises(CrossProcessDeliveryError, match="mismatched"):
        sender.deliver_once("indicator", "risk", receiver_accept=forged,
                            now_ms=BASE+300, lease_ms=10)
    assert source.receipts("indicator", "risk") == []


def test_receiver_corruption_detected_during_durable_read(tmp_path):
    sender, _, target, _ = setup(tmp_path, count=1)
    send(sender, target)
    with sqlite3.connect(target.path) as db:
        db.execute("""
            UPDATE offline_independent_projection_receipts
            SET projection_json='{"price":"0"}'
        """)
    with pytest.raises(CrossProcessDeliveryError):
        target.receipts("indicator", "risk")


def test_independent_receiver_separation_by_stable_receiver_name(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    risk = send(sender, target)
    research = sender.deliver_once(
        "indicator", "research",
        receiver_accept=lambda env: target.accept(env, now_ms=BASE+301),
        now_ms=BASE+301,
    )
    assert risk.receipt_id != research.receipt_id
    assert len(target.receipts("indicator", "research")) == 1
    assert len(target.receipts("indicator", "risk")) == 1


def test_multiple_os_threads_atomic_duplicate_receiver_admission(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    claim = source.claim_next("indicator", "risk", now_ms=BASE+300)
    env = sender.envelope(claim)
    with ThreadPoolExecutor(max_workers=8) as pool:
        returned = list(pool.map(
            lambda _: target.accept(env, now_ms=BASE+310), range(12),
        ))
    assert len(set(r.receipt_id for r in returned)) == 1
    assert len(target.receipts("indicator", "risk")) == 1


def test_receiver_accept_in_separate_python_process_and_restart(tmp_path):
    sender, source, target, _ = setup(tmp_path, count=1)
    captured = []
    def transport(env):
        program = """
import json,sys
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope,
)
args=json.loads(sys.stdin.read())
target=OfflineIndependentProjectionReceiver(
    args["path"],mode_provider=lambda:QuantMode.SIMULATION,
)
envelope=ProjectionEnvelope(**args["env"])
receipt=target.accept(envelope,now_ms=args["now"])
print(json.dumps(receipt.__dict__,sort_keys=True))
"""
        packet = json.dumps({
            "path": str(target.path),
            "env": env.__dict__,
            "now": BASE+300,
        })
        p = subprocess.run(
            [sys.executable, "-c", program],
            input=packet, text=True, capture_output=True,
            timeout=20, check=True,
        )
        captured.append(p.stdout)
        return ReceiverOwnedReceipt(**json.loads(p.stdout))
    receipt = sender.deliver_once(
        "indicator", "risk", receiver_accept=transport,
        now_ms=BASE+300,
    )
    assert len(captured) == 1
    assert receipt == target.receipts("indicator", "risk")[0]
    assert source.receipts("indicator", "risk")[0].result == receipt.acknowledgement()


def test_reality_mode_blocks_both_sender_and_receiver(tmp_path):
    mode = [QuantMode.SIMULATION]
    sender, source, target, _ = setup(tmp_path, count=1, mode=mode)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        send(sender, target)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        target.receipts("indicator", "risk")
