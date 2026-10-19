"""Phase 1 latency profiling is opt-in, bounded and never certifies a source."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from infrastructure.spot_forward_stage_profile import BoundedStageProfiler
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import source, snapshot, _NOW


def test_profile_aggregate_only_bounded_and_no_overwrite(tmp_path):
    root=tmp_path/"new"
    root.mkdir()
    p=BoundedStageProfiler(root/"stage_latency.json")
    for _ in range(4099):
        with p.measure("source_sqlite_commit"):
            pass
    with pytest.raises(ValueError,match="unrecognized"):
        with p.measure("private_path"):
            pass
    report=p.summary(completed=False)
    assert report["source_complete"] is False
    assert report["stages"]["source_sqlite_commit"]["samples"]==4096
    assert report["stages"]["source_sqlite_commit"]["sample_limit_reached"]
    assert "original" not in json.dumps(report["stages"])
    p.write(completed=False)
    assert (root/"stage_latency.json").is_file()
    assert (root/"stage_latency.json").read_text().count("source_sqlite_commit") == 1


@pytest.mark.asyncio
async def test_no_implicit_profile_when_disabled(tmp_path,monkeypatch):
    monkeypatch.delenv("ECONITH_PHASE1_STAGE_PROFILE",raising=False)
    root=(tmp_path/"no_prof").resolve()
    status=await observe_forward_spot_research(
        output_dir=root,session_id="rec_no_profile",
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    )
    assert status.session_complete
    assert not (root/"stage_latency.json").exists()


@pytest.mark.asyncio
async def test_profile_records_real_receiver_and_derived_stages(tmp_path,monkeypatch):
    monkeypatch.setenv("ECONITH_PHASE1_STAGE_PROFILE","1")
    root=(tmp_path/"prof").resolve()
    status=await observe_forward_spot_research(
        output_dir=root,session_id="rec_with_profile",
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    )
    assert status.session_complete
    p=json.loads((root/"stage_latency.json").read_text())
    assert p["source_complete"] is True
    assert p["raw_event_contents_recorded"] is False
    assert p["stages"]["source_sqlite_commit"]["samples"] == status.original_records
    assert p["stages"]["receiver_hmac_ack"]["samples"] == status.receiver_receipts
    assert p["stages"]["checkpoint_commit"]["samples"] == status.receiver_receipts
    assert p["stages"]["source_verify"]["samples"] == status.original_records
    assert p["stages"]["derived_subscriber_wait"]["samples"] == status.receiver_receipts


@pytest.mark.asyncio
async def test_profile_writes_incomplete_on_source_rejection(tmp_path,monkeypatch):
    monkeypatch.setenv("ECONITH_PHASE1_STAGE_PROFILE","1")
    root=(tmp_path/"failed").resolve()
    async def definitely_stale_source():
        async for frame in source(_NOW()):
            # The original 150ms fixture can coincide with a newer depth
            # receipt on a busy Windows host. Force a >350ms trade delay.
            item=json.loads(frame)
            if item["data"]["e"]=="aggTrade" and item["data"]["a"]==500:
                await asyncio.sleep(0.35)
            yield frame
    with pytest.raises(Exception,match="stale synchronized book"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_profile_failure",
            stream=definitely_stale_source(),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,persistent_receiver=True,
            max_book_staleness_ms=100,
        )
    p=json.loads((root/"stage_latency.json").read_text())
    assert p["source_complete"] is False
    assert p["stages"]["source_sqlite_commit"]["samples"] >= 3
    assert "receiver_hmac_ack" in p["stages"]
