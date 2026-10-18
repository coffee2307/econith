"""Reconstruct eligible un-STAGED forwards from ORIGINAL receipt time only."""
from __future__ import annotations

import sqlite3
import time

import pytest

from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox, ForwardOutboxError
from infrastructure.spot_forward_eligibility import (
    ForwardEligibilityError, rescan_forward_eligibility,
)
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from core.mode import QuantMode
from tests.test_spot_forward_verified_research import source, snapshot, _NOW


@pytest.mark.asyncio
async def test_crash_after_raw_commit_before_first_stage_rescans_as_of(tmp_path, monkeypatch):
    root=(tmp_path/"before_stage").resolve()
    original=OfflineForwardSpotOutbox.stage

    def fail_before_stage(self, envelope):
        raise RuntimeError("crash window: original already committed, intent absent")

    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",fail_before_stage)
    with pytest.raises(RuntimeError,match="intent absent"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_rescan_first",
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,
        )
    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",original)
    with sqlite3.connect(root/"original.sqlite") as db:
        orig=db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0]
        inventory=db.execute("SELECT COUNT(*) FROM offline_forward_capture_intents").fetchone()[0]
    assert orig == inventory and orig >= 3
    outbox=OfflineForwardSpotOutbox(
        root/"forward_outbox.sqlite",session_id="rec_rescan_first",
        mode_provider=lambda:QuantMode.SIMULATION,
    )
    assert outbox.items()==[]
    result=await recover_staged_forward_spot(
        root=root,session_id="rec_rescan_first",reconstruct_missing=True,
    )
    assert result.original_eligibility_rescan_verified is True
    assert result.reconstructed_missing_intents==1
    assert result.outbox_derived_acked==result.receiver_receipts==1
    assert result.primary_derived_receipts==1
    assert result.independent_named_receipts==2
    assert result.source_coverage_certified is False
    assert result.session_certified is False
    assert result.orders_submitted==0
    # First aggTrade arrived BEFORE depth synchronization: no future book
    # can be borrowed retroactively to certify a ticker.
    assert outbox.items()[0].envelope.projection_json.find('"md.depth"')>=0
    again=await recover_staged_forward_spot(
        root=root,session_id="rec_rescan_first",reconstruct_missing=True,
    )
    assert again.reconstructed_missing_intents==0
    assert again.outbox_derived_acked==1


@pytest.mark.asyncio
async def test_resume_after_partial_staged_graph_rebuilds_later_eligible_original(tmp_path,monkeypatch):
    root=(tmp_path/"middle_crash").resolve()
    original=OfflineForwardSpotOutbox.stage
    hits=0

    def fail_fourth(self, envelope):
        nonlocal hits
        hits+=1
        if hits==4:
            raise RuntimeError("crash before staging fourth eligible record")
        return original(self,envelope)

    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",fail_fourth)
    with pytest.raises(RuntimeError,match="fourth eligible"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_rescan_later",
            stream=source(_NOW()),snapshot_fetch=snapshot,max_frames=20,timeout_s=20,
        )
    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",original)
    report=await recover_staged_forward_spot(
        root=root,session_id="rec_rescan_later",reconstruct_missing=True,
    )
    assert report.reconstructed_missing_intents==1
    assert report.staged_envelopes==report.receiver_receipts==4
    assert report.primary_derived_receipts==6
    assert report.independent_named_receipts==12
    assert report.outbox_derived_acked==4
    assert not report.session_certified


@pytest.mark.asyncio
async def test_previously_staged_forged_projection_blocks_rescan_before_any_new_stage(tmp_path,monkeypatch):
    root=(tmp_path/"conflict").resolve()
    original=OfflineForwardSpotOutbox.ack_derived
    def abort(self,e):
        raise RuntimeError("stop after first research admission")
    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_derived",abort)
    with pytest.raises(RuntimeError):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_rescan_conflict",
            stream=source(_NOW()),snapshot_fetch=snapshot,max_frames=20,timeout_s=20,
        )
    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_derived",original)
    db=root/"forward_outbox.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("""
            UPDATE forward_outbox SET projection_fingerprint=? WHERE ingest_id=(
                SELECT MIN(ingest_id) FROM forward_outbox)
        """,("0"*64,))
    with pytest.raises(ForwardOutboxError,match="reconstructed as-of source"):
        await recover_staged_forward_spot(
            root=root,session_id="rec_rescan_conflict",reconstruct_missing=True,
        )


@pytest.mark.asyncio
async def test_original_source_integrity_violation_prevents_reconstruction(tmp_path,monkeypatch):
    root=(tmp_path/"tamper").resolve()
    original=OfflineForwardSpotOutbox.stage
    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",
                        lambda *a,**kw: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_rescan_tamper",
            stream=source(_NOW()),snapshot_fetch=snapshot,max_frames=20,timeout_s=20,
        )
    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",original)
    with sqlite3.connect(root/"original.sqlite") as conn:
        conn.execute("""
            UPDATE offline_spot_capture_records SET raw_blob=? WHERE capture_id=(
                SELECT MAX(capture_id) FROM offline_spot_capture_records)
        """,(b'{"invalid":true}',))
    with pytest.raises(Exception):
        await recover_staged_forward_spot(
            root=root,session_id="rec_rescan_tamper",reconstruct_missing=True,
        )


@pytest.mark.asyncio
async def test_explicit_unstaged_rescan_is_required(tmp_path,monkeypatch):
    root=(tmp_path/"no_auto").resolve()
    original=OfflineForwardSpotOutbox.stage
    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",
                        lambda *a,**kw: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_rescan_explicit",
            stream=source(_NOW()),snapshot_fetch=snapshot,max_frames=20,timeout_s=20,
        )
    monkeypatch.setattr(OfflineForwardSpotOutbox,"stage",original)
    with pytest.raises(ForwardOutboxError,match="no staged"):
        await recover_staged_forward_spot(
            root=root,session_id="rec_rescan_explicit",
        )
    assert not (root/".forward_producer.lock").exists()
