"""Opt-in in-session source-first projection: independent cursor & fail-closed lag.

Synthetic fixture data are not authenticated public venue evidence.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from ci.phase1_offline_burst_probe import _source, _snapshot
from infrastructure.spot_forward_verified_research import (
    ForwardSpotResearchError, observe_forward_spot_research,
)
from infrastructure.spot_projection_durable_cursor import (
    OfflineSpotProjectionCursor, ProjectionCursorError,
)
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from core.mode import QuantMode
from tests.test_spot_forward_verified_research import source, _NOW


@pytest.mark.asyncio
async def test_spool_projects_before_source_closes_and_seals_consistently(tmp_path):
    root=(tmp_path/"open_spool").resolve()
    release=asyncio.Event()
    task=asyncio.create_task(observe_forward_spot_research(
        output_dir=root, session_id="rec_live_spool_test",
        stream=source(_NOW(),until=release),snapshot_fetch=_snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
        spooled_in_session=True,max_projector_lag_ms=5000,
    ))
    try:
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            db=root/"independent_receiver.sqlite"
            if db.is_file():
                with sqlite3.connect(db) as con:
                    count=con.execute(
                        "SELECT COUNT(*) FROM offline_independent_projection_receipts"
                    ).fetchone()[0]
                if count>=3:
                    with sqlite3.connect(root/"original.sqlite") as con:
                        assert con.execute(
                            "SELECT closed_at_ms FROM offline_spot_capture_sessions"
                        ).fetchone()[0] is None
                    break
            await asyncio.sleep(0.03)
        else:
            raise AssertionError("no durable in-session receiver receipt")
    finally:
        release.set()
    result=await task
    assert result.session_complete
    assert result.publication_in_session
    assert result.original_records==6
    assert result.receiver_receipts==4
    assert result.spool_original_cursor==6
    assert result.max_spool_pending_originals>=1
    assert 0 <= result.max_projector_lag_ms <= 5000
    cursor=OfflineSpotProjectionCursor(
        root/"projection_cursor.sqlite",
        journal=OfflineSpotCaptureJournal(
            root/"original.sqlite",mode_provider=lambda:QuantMode.SIMULATION,
            forward_capture_audit=True,
        ),session_id="rec_live_spool_test",
    )
    assert cursor.read_verified().last_capture_id==6


@pytest.mark.asyncio
async def test_spool_small_pending_limit_quarantines_without_completion(tmp_path):
    root=(tmp_path/"pending").resolve()
    with pytest.raises(ForwardSpotResearchError,match="pending capacity exceeded"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_spool_overloaded",
            stream=_source(50,0),snapshot_fetch=_snapshot,
            max_frames=50,timeout_s=30,persistent_receiver=True,
            spooled_in_session=True,max_pending_originals=1,
        )
    assert (root/"original.sqlite").is_file()
    assert not (root/"forward_complete.json").exists()


@pytest.mark.asyncio
async def test_spool_rejects_public_without_separate_approval(tmp_path):
    root=(tmp_path/"public_disabled").resolve()
    with pytest.raises(PermissionError,match="public continuous spool"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_spool_public",
            allow_public_network=True,source="binance_public_simulation",
            spooled_in_session=True,persistent_receiver=True,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_spool_rejects_old_projector_lag_without_relabeling(tmp_path,monkeypatch):
    # Slow only the independent ACK to force a source->publication lag breach;
    # the already-committed source is forensic evidence, NOT acceptance.
    from infrastructure.market_persistent_hmac_receiver import PersistentHmacResearchReceiver
    original=PersistentHmacResearchReceiver.accept_batch
    async def slow_ack(self,*args,**kwargs):
        await asyncio.sleep(.22)
        return await original(self,*args,**kwargs)
    monkeypatch.setattr(PersistentHmacResearchReceiver,"accept_batch",slow_ack)
    root=(tmp_path/"late").resolve()
    with pytest.raises(ForwardSpotResearchError,match="source delivery lag exceeded"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_spool_late",
            stream=_source(30,5),snapshot_fetch=_snapshot,
            max_frames=30,timeout_s=30,
            persistent_receiver=True,spooled_in_session=True,
            max_projector_lag_ms=100,max_pending_originals=100,
        )
    assert (root/"original.sqlite").is_file()
    assert not (root/"forward_complete.json").exists()


@pytest.mark.asyncio
async def test_crash_after_checkpoint_before_cursor_never_certifies_source(tmp_path,monkeypatch):
    # Crash at a known cross-database boundary: receiver/derived/checkpoint
    # have COMMITTED, source cursor has not. Never pretend it was atomic.
    original=OfflineSpotProjectionCursor.advance
    advanced=[0]
    def injected_failure(self,record):
        advanced[0]+=1
        if advanced[0]==3:
            raise RuntimeError("injected post-ACK pre-cursor crash")
        return original(self,record)
    monkeypatch.setattr(OfflineSpotProjectionCursor,"advance",injected_failure)
    root=(tmp_path/"post_checkpoint").resolve()
    with pytest.raises(Exception):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_post_checkpoint",
            stream=source(_NOW()),snapshot_fetch=_snapshot,
            max_frames=20,timeout_s=20,persistent_receiver=True,
            spooled_in_session=True,
        )
    assert (root/"original.sqlite").is_file()
    assert not (root/"forward_complete.json").exists()
    with sqlite3.connect(root/"original.sqlite") as con:
        assert con.execute(
            "SELECT closed_at_ms FROM offline_spot_capture_sessions"
        ).fetchone()[0] is not None
    with sqlite3.connect(root/"projection_cursor.sqlite") as con:
        cursor=con.execute(
            "SELECT last_capture_id FROM spot_projection_cursor"
        ).fetchone()[0]
    with sqlite3.connect(root/"forward_checkpoints.sqlite") as con:
        checkpoint_count=con.execute(
            "SELECT COUNT(*) FROM forward_checkpoint"
        ).fetchone()[0]
    assert 0 <= cursor < advanced[0]
    assert checkpoint_count>=1
