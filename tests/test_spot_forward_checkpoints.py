"""Source-bound forward checkpoints while stream OPEN, tamper and crash repair."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.spot_forward_checkpoints import (
    ForwardCheckpointError,OfflineForwardSessionCheckpoints,
)
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import _NOW,source,snapshot


def check(root,sid):
    return OfflineForwardSessionCheckpoints(
        root/"forward_checkpoints.sqlite",
        session_id=sid,mode_provider=lambda:QuantMode.SIMULATION,
    )


def durable(root,sid):
    return OfflineForwardSpotOutbox(
        root/"forward_outbox.sqlite",
        session_id=sid,mode_provider=lambda:QuantMode.SIMULATION,
    ).items()


@pytest.mark.asyncio
async def test_checkpoint_is_durable_during_open_source_session(tmp_path):
    root=(tmp_path/"open_source").resolve()
    release=asyncio.Event()
    sid="rec_live_checkpoint"
    task=asyncio.create_task(observe_forward_spot_research(
        output_dir=root,session_id=sid,
        stream=source(_NOW(),until=release),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    ))
    try:
        deadline=asyncio.get_running_loop().time()+15
        while True:
            if (root/"forward_checkpoints.sqlite").is_file():
                with sqlite3.connect(root/"forward_checkpoints.sqlite") as db:
                    count=db.execute(
                        "SELECT COUNT(*) FROM forward_checkpoint"
                    ).fetchone()[0]
                if count>=3:
                    break
            if asyncio.get_running_loop().time()>deadline:
                raise AssertionError("no durable checkpoint while capture OPEN")
            await asyncio.sleep(0.03)
        with sqlite3.connect(root/"original.sqlite") as db:
            original=db.execute(
                "SELECT closed_at_ms FROM offline_spot_capture_sessions"
            ).fetchone()
        assert original[0] is None
        current=check(root,sid).verified_prefix(durable(root,sid))
        assert current.checkpointed_events==3
        assert current.last_capture_id is not None
        assert len(current.tail_sha256)==64
        assert not current.market_feed_continuous
        assert not current.external_history_complete
    finally:
        release.set()
    result=await task
    state=check(root,sid).verified_prefix(durable(root,sid))
    assert result.session_complete
    assert result.forward_checkpoints==state.checkpointed_events==4
    assert result.forward_checkpoint_sha256==state.tail_sha256
    assert result.orders_submitted==0


@pytest.mark.asyncio
async def test_checkpoint_tamper_rejected_by_independent_audit_and_recovery(tmp_path):
    root=(tmp_path/"corrupt_tail").resolve()
    sid="rec_checkpoint_tamper"
    result=await observe_forward_spot_research(
        output_dir=root,session_id=sid,
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    )
    assert result.forward_checkpoints==4
    with sqlite3.connect(root/"forward_checkpoints.sqlite") as db:
        db.execute(
            "UPDATE forward_checkpoint SET checkpoint_sha256=? WHERE ordinal=2",
            ("f"*64,),
        )
    with pytest.raises(ForwardCheckpointError,match="hash/source mismatch"):
        check(root,sid).verified_prefix(durable(root,sid))
    with pytest.raises(ForwardCheckpointError,match="hash/source mismatch"):
        await recover_staged_forward_spot(root=root,session_id=sid)


@pytest.mark.asyncio
async def test_missing_middle_checkpoint_rejected_even_if_all_outbox_acked(tmp_path):
    root=(tmp_path/"missing_middle").resolve()
    sid="rec_checkpoint_middle"
    result=await observe_forward_spot_research(
        output_dir=root,session_id=sid,
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    )
    assert result.forward_checkpoints==4
    with sqlite3.connect(root/"forward_checkpoints.sqlite") as db:
        db.execute("DELETE FROM forward_checkpoint WHERE ordinal=2")
    with pytest.raises(ForwardCheckpointError,match="hash/source mismatch"):
        check(root,sid).verified_prefix(durable(root,sid))
    with pytest.raises(ForwardCheckpointError):
        await recover_staged_forward_spot(root=root,session_id=sid)


@pytest.mark.asyncio
async def test_crash_after_derived_outbox_ack_reconciles_only_missing_suffix(
    tmp_path,monkeypatch,
):
    root=(tmp_path/"before_checkpoint").resolve()
    sid="rec_checkpoint_crash"
    original=OfflineForwardSessionCheckpoints.append

    def crash_before_checkpoint(self,*a,**kw):
        raise RuntimeError("injected crash before checkpoint COMMIT")

    monkeypatch.setattr(OfflineForwardSessionCheckpoints,"append",crash_before_checkpoint)
    with pytest.raises(RuntimeError,match="before checkpoint"):
        await observe_forward_spot_research(
            output_dir=root,session_id=sid,
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,persistent_receiver=True,
        )
    monkeypatch.setattr(OfflineForwardSessionCheckpoints,"append",original)
    assert check(root,sid).verified_prefix(durable(root,sid)).checkpointed_events==0
    assert durable(root,sid)[0].status=="DERIVED_ACKED"
    report=await recover_staged_forward_spot(
        root=root,session_id=sid,reconstruct_missing=True,
    )
    assert report.outbox_derived_acked==1
    assert report.forward_checkpoints==1
    assert report.checkpoint_suffix_repaired==1
    assert not report.session_certified
    again=await recover_staged_forward_spot(
        root=root,session_id=sid,reconstruct_missing=True,
    )
    assert again.forward_checkpoints==1
    assert again.checkpoint_suffix_repaired==0
    assert check(root,sid).verified_prefix(durable(root,sid)).checkpointed_events==1


@pytest.mark.asyncio
async def test_checkpoint_source_identity_must_not_be_rebound(tmp_path):
    root=(tmp_path/"identity").resolve()
    sid="rec_checkpoint_owner"
    await observe_forward_spot_research(
        output_dir=root,session_id=sid,
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    )
    with pytest.raises(ForwardCheckpointError,match="identity changed"):
        check(root,"rec_other_source")
    with pytest.raises(ForwardCheckpointError,match="SIMULATION"):
        OfflineForwardSessionCheckpoints(
            root/"forward_checkpoints.sqlite",
            session_id=sid,mode_provider=lambda:QuantMode.REALITY,
        )
