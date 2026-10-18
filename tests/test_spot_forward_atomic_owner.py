"""Phase 1: source-commit inventory atomicity and forward ownership safety."""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal, SpotCaptureError
from infrastructure.spot_forward_owner import (
    ForwardOwnershipDenied, exclusive_forward_owner,
)
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import (
    _NOW, frame, trade, source, snapshot,
)

_WS = "wss://data-stream.binance.vision/stream"


def started(tmp_path):
    journal=OfflineSpotCaptureJournal(
        tmp_path/"original.sqlite",
        mode_provider=lambda:QuantMode.SIMULATION,
        forward_capture_audit=True,
    )
    now=_NOW()
    journal.start_session(
        "rec_atomic_inventory",symbol="BTCUSDT",source="offline_fixture",
        ws_endpoint=_WS,opened_at_ms=now,
    )
    return journal,now


def test_original_and_source_intent_commit_in_same_sqlite_transaction(tmp_path):
    journal,now=started(tmp_path)
    capture_id=journal.record_ws(
        "rec_atomic_inventory",frame(trade(501,now)),
        stream_kind="aggTrade",received_at_ms=now,
    )
    with journal._connect() as db:
        source=db.execute(
            "SELECT capture_id,sha256 FROM offline_spot_capture_records"
        ).fetchone()
        receipt=db.execute("""
            SELECT capture_id,sha256 FROM offline_forward_capture_intents
        """).fetchone()
    assert source["capture_id"]==receipt["capture_id"]==capture_id
    assert source["sha256"]==receipt["sha256"]
    verified=journal.verify_forward_capture_inventory("rec_atomic_inventory")
    assert len(verified)==1 and verified[0].capture_id==capture_id


def test_forward_intent_write_failure_rolls_back_raw_commit(tmp_path):
    journal,now=started(tmp_path)
    with journal._connect() as db:
        db.execute("""
            CREATE TRIGGER deny_intent BEFORE INSERT ON offline_forward_capture_intents
            BEGIN SELECT RAISE(ABORT, 'simulated intent disk failure'); END
        """)
    with pytest.raises(sqlite3.DatabaseError,match="simulated intent disk failure"):
        journal.record_ws(
            "rec_atomic_inventory",frame(trade(501,now)),
            stream_kind="aggTrade",received_at_ms=now,
        )
    with journal._connect() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_spot_capture_records"
        ).fetchone()[0]==0
        assert db.execute(
            "SELECT COUNT(*) FROM offline_forward_capture_intents"
        ).fetchone()[0]==0


def test_missing_or_tampered_atomic_source_intent_is_detected(tmp_path):
    journal,now=started(tmp_path)
    journal.record_ws(
        "rec_atomic_inventory",frame(trade(501,now)),
        stream_kind="aggTrade",received_at_ms=now,
    )
    with journal._connect() as db:
        db.execute("DELETE FROM offline_forward_capture_intents")
    with pytest.raises(SpotCaptureError,match="intent missing"):
        journal.verify_forward_capture_inventory("rec_atomic_inventory")


@pytest.mark.asyncio
async def test_running_forward_owner_blocks_racing_recovery(tmp_path):
    root=(tmp_path/"exclusive").resolve()
    unblock=asyncio.Event()
    task=asyncio.create_task(observe_forward_spot_research(
        output_dir=root,session_id="rec_owner_live",
        stream=source(_NOW(),until=unblock),snapshot_fetch=snapshot,
        timeout_s=20,max_frames=20,
    ))
    try:
        deadline=asyncio.get_running_loop().time()+10
        while not (root/".forward_producer.lock").exists():
            if asyncio.get_running_loop().time()>deadline:
                raise AssertionError("producer never acquired exclusive ownership")
            await asyncio.sleep(0.02)
        with pytest.raises(ForwardOwnershipDenied,match="operator review"):
            await recover_staged_forward_spot(root=root,session_id="rec_owner_live")
    finally:
        unblock.set()
    result=await task
    assert result.session_complete
    assert not (root/".forward_producer.lock").exists()


@pytest.mark.asyncio
async def test_orphaned_owner_lock_refuses_recovery_until_operator_review(tmp_path):
    root=(tmp_path/"orphan").resolve()
    result=await observe_forward_spot_research(
        output_dir=root,session_id="rec_owner_orphan",
        stream=source(_NOW()),snapshot_fetch=snapshot,
        timeout_s=20,max_frames=20,
    )
    assert result.session_complete
    lock=root/".forward_producer.lock"
    lock.write_text("orphaned hard-exit marker")
    with pytest.raises(ForwardOwnershipDenied,match="operator review"):
        await recover_staged_forward_spot(root=root,session_id="rec_owner_orphan")
    assert lock.exists()
    # In production an operator must inspect the original process and
    # evidence, then explicitly release an orphan lock. Test only.
    lock.unlink()
    replay=await recover_staged_forward_spot(root=root,session_id="rec_owner_orphan")
    assert replay.outbox_derived_acked==4
    assert replay.orders_submitted==0
