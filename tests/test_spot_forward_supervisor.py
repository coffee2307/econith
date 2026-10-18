"""Finite multi-session forward research endurance controls (offline fixtures)."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from infrastructure.spot_forward_supervisor import (
    ForwardSupervisorError, _verify_one, run_forward_research_supervisor,
)
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import _NOW, snapshot, source


async def genuine_fixture(root, session_id, iteration):
    # Actual immutable source -> post-COMMIT shadow -> HMAC child ->
    # independently owned consumer SQLite chain, never a mocked success.
    return await observe_forward_spot_research(
        output_dir=root,session_id=session_id,
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,
    )


def _db(root):
    return sqlite3.connect(root/"forward_supervisor.sqlite")


@pytest.mark.asyncio
async def test_failed_socket_opens_outage_next_fresh_session_restores_current_only(tmp_path):
    root=(tmp_path/"research_trials").resolve()
    async def sometimes_fails(directory, sid, attempt):
        if attempt == 2:
            raise ConnectionResetError("forced disconnect with no independent ACK")
        return await genuine_fixture(directory,sid,attempt)

    state=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,attempts=3,
        max_frames=20,timeout_s=20,capture_factory=sometimes_fails,
    )
    assert state.mode=="SIMULATION"
    assert state.state=="FINISHED_BOUNDED_RESEARCH"
    assert state.attempts==3
    assert state.accepted_sessions==2
    assert state.failed_sessions==1
    assert state.incomplete_attempts==0
    assert state.outage_windows==1
    assert state.open_outages==0
    assert state.total_receiver_receipts==8
    assert state.total_originals==12
    assert state.max_queue_high_watermark>0
    assert state.peak_recorded_storage_bytes>0
    assert not state.history_continuity_verified
    assert not state.historical_gaps_recovered
    assert not state.continuous_live_market
    assert not state.execution_started
    assert state.orders_submitted==0
    with _db(root) as db:
        rows=db.execute("""
            SELECT iteration,session_id,status,failure_code,receiver_acks
            FROM forward_watch_attempt ORDER BY iteration
        """).fetchall()
        outage=db.execute("""
            SELECT opened_ms,restored_ms,coverage_verified,last_failure_code
            FROM forward_watch_outage
        """).fetchone()
    assert [r[2] for r in rows]==["PASSED","FAILED","PASSED"]
    assert len({row[1] for row in rows})==3
    assert rows[1][3]=="ConnectionResetError"
    assert rows[0][4]==rows[2][4]==4
    assert rows[1][4]==0
    assert outage[1]>=outage[0]
    assert outage[2]==0  # fresh book != historical gap recovery
    assert outage[3]=="ConnectionResetError"
    assert not (root/"attempt_002"/"original.sqlite").exists()
    assert (root/"attempt_003"/"original.sqlite").exists()
    assert not (root/".forward_producer.lock").exists()


@pytest.mark.asyncio
async def test_supervisor_never_accepts_unbacked_or_forged_session_receipt(tmp_path):
    root=(tmp_path/"no_source").resolve()
    from infrastructure.spot_forward_verified_research import ForwardSpotResearchStatus

    async def fabricated(directory,sid,index):
        # Source/receiver DBs do not exist, even though claimed fields look
        # like a successful forward graph.
        return ForwardSpotResearchStatus(
            mode="SIMULATION",session_id=sid,
            original_records=6,verified_trades=3,
            forwarded_trades=2,forwarded_books=2,
            pre_sync_trades_not_forwarded=1,receiver_receipts=4,
            primary_derived_receipts=6,named_derived_receipts=12,
            book_final_update_id=103,all_receipts_independent=True,
            research_only=True,publication_in_session=True,
            session_complete=True,history_continuity_verified=False,
            continuous_live_market=False,execution_started=False,
            orders_submitted=0,queue_high_watermark=1,
        )

    state=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,attempts=1,
        max_frames=20,capture_factory=fabricated,
    )
    assert state.accepted_sessions==0
    assert state.failed_sessions==1
    assert state.open_outages==1
    assert not state.history_continuity_verified
    with _db(root) as db:
        reason=db.execute(
            "SELECT failure_code FROM forward_watch_attempt"
        ).fetchone()[0]
    assert reason=="ForwardSupervisorError"


@pytest.mark.asyncio
async def test_preflight_rejection_is_durable_and_opens_no_socket(tmp_path):
    root=(tmp_path/"bad_clock").resolve()
    opened=[]
    async def bad_clock():
        return False
    async def never(directory,sid,index):
        opened.append(index)
        raise AssertionError("public market socket must not start")
    with pytest.raises(ForwardSupervisorError,match="clock"):
        await run_forward_research_supervisor(
            output_dir=root,allow_public_network=True,
            attempts=2,max_frames=20,
            capture_factory=never,clock_check=bad_clock,
        )
    assert not opened
    with _db(root) as db:
        row=db.execute(
            "SELECT status,failure_code FROM forward_watch_attempt"
        ).fetchone()
        assert row==("FAILED","ForwardSupervisorError")
        assert db.execute(
            "SELECT COUNT(*) FROM forward_watch_outage"
        ).fetchone()[0]==1
    assert not (root/"attempt_001").exists()
    assert not (root/".forward_producer.lock").exists()


@pytest.mark.asyncio
async def test_cancellation_keeps_start_marker_without_false_success(tmp_path):
    root=(tmp_path/"stopped").resolve()
    entered=asyncio.Event()
    block=asyncio.Event()
    async def held(directory,sid,index):
        entered.set()
        await block.wait()
        return await genuine_fixture(directory,sid,index)
    task=asyncio.create_task(run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,
        attempts=2,max_frames=20,capture_factory=held,
    ))
    await asyncio.wait_for(entered.wait(),timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with _db(root) as db:
        rows=db.execute(
            "SELECT status FROM forward_watch_attempt"
        ).fetchall()
    assert rows==[("STARTED",)]
    assert not (root/".forward_producer.lock").exists()
    with pytest.raises(ValueError,match="configuration"):
        await run_forward_research_supervisor(
            output_dir=root,allow_public_network=True,
            attempts=1,max_frames=20,capture_factory=held,
        )


@pytest.mark.asyncio
async def test_bounded_policy_rejects_unapproved_network_and_storage_limits(tmp_path):
    root=(tmp_path/"refused").resolve()
    with pytest.raises(PermissionError):
        await run_forward_research_supervisor(output_dir=root)
    for kwargs in (
        {"attempts":17},{"attempts":16,"max_frames":2001},
        {"max_frames":9},{"timeout_s":500},
        {"max_total_storage_bytes":100},
        {"max_book_staleness_ms":0},
    ):
        with pytest.raises(ValueError):
            await run_forward_research_supervisor(
                output_dir=root,allow_public_network=True,
                capture_factory=genuine_fixture,**kwargs,
            )
    with pytest.raises(ForwardSupervisorError,match="clock"):
        await run_forward_research_supervisor(
            output_dir=root,allow_public_network=True,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_source_alteration_is_detected_independently_after_success(tmp_path):
    root=(tmp_path/"altered_source").resolve()
    state=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,attempts=1,
        max_frames=20,timeout_s=20,capture_factory=genuine_fixture,
    )
    assert state.accepted_sessions==1
    with _db(root) as db:
        sid=db.execute(
            "SELECT session_id FROM forward_watch_attempt"
        ).fetchone()[0]
    source_dir=root/"attempt_001"
    with sqlite3.connect(source_dir/"original.sqlite") as db:
        db.execute("""
            UPDATE offline_spot_capture_records SET raw_blob=?
            WHERE capture_id=(SELECT MIN(capture_id)
                               FROM offline_spot_capture_records)
        """,(b'{"tampered":true}',))
    from infrastructure.spot_forward_verified_research import ForwardSpotResearchStatus
    forged=ForwardSpotResearchStatus(
        mode="SIMULATION",session_id=sid,original_records=6,
        verified_trades=3,forwarded_trades=2,forwarded_books=2,
        pre_sync_trades_not_forwarded=1,receiver_receipts=4,
        primary_derived_receipts=6,named_derived_receipts=12,
        book_final_update_id=103,all_receipts_independent=True,
        research_only=True,publication_in_session=True,
        session_complete=True,history_continuity_verified=False,
        continuous_live_market=False,execution_started=False,
        orders_submitted=0,queue_high_watermark=1,
    )
    with pytest.raises(Exception):
        _verify_one(source_dir,forged)


@pytest.mark.asyncio
async def test_receiver_receipt_fingerprint_tamper_rejected_after_success(tmp_path):
    root=(tmp_path/"altered_ack").resolve()
    captured={}
    async def capture(directory,sid,index):
        value=await genuine_fixture(directory,sid,index)
        captured["status"]=value
        return value
    outcome=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,attempts=1,
        max_frames=20,timeout_s=20,capture_factory=capture,
    )
    assert outcome.accepted_sessions==1
    with sqlite3.connect(
        root/"attempt_001"/"independent_receiver.sqlite"
    ) as db:
        db.execute("""
            UPDATE offline_independent_projection_receipts
            SET projection_fingerprint=?
            WHERE ingest_id=(
                SELECT MIN(ingest_id)
                FROM offline_independent_projection_receipts
            )
        """,("0"*64,))
    with pytest.raises(ForwardSupervisorError,match="independent receipt"):
        _verify_one(root/"attempt_001",captured["status"])
