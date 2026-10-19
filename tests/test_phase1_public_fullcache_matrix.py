"""All-connection-cache finite public operator flags remain explicit and gated."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

from ci import phase1_public_live_spool_matrix as operator


def accepted(frames: int) -> dict:
    return {
        "original_records":frames+1,
        "receiver_receipts":frames-8,
        "forward_checkpoints":frames-8,
        "spool_original_cursor":frames+1,
        "queue_high_watermark":38,
        "max_projector_lag_ms":930,
        "max_spool_pending_originals":60,
        "publication_in_session":True,
        "projector_age_samples":frames-8,
        "projector_p50_admission_age_upper_ms":199,
        "projector_p95_admission_age_upper_ms":849,
        "projector_p99_admission_age_upper_ms":949,
        "projector_min_headroom_ms":4070,
        "orders_submitted":0,
    }


@pytest.mark.asyncio
async def test_explicit_full_cache_flags_and_per_trial_host_guard(tmp_path,monkeypatch):
    called=[]
    async def fake_run(path:Path,**kwargs):
        called.append(kwargs)
        path.mkdir()
        return accepted(kwargs["frames"])
    snapshots=[]
    def host(root):
        snapshots.append(root)
        return {
            "available_memory_bytes":2_300_000_000,
            "disk_free_bytes":8_000_000_000,
            "evidence_file_bytes":12_000_000,
        }
    monkeypatch.setattr(operator,"run",fake_run)
    monkeypatch.setattr(operator,"host_snapshot",host)
    root=(tmp_path/"matrix").resolve()
    root.mkdir()
    result=await operator.matrix(
        root,frames=1000,max_lag_ms=5000,
        reuse_derived_connections=True,
        reuse_producer_connections=True,
        reuse_receiver_connection=True,
        reuse_original_connection=True,
        diagnose_pressure=True,resource_guard=True,
        cooperative_reader_yield=True,
    )
    assert result["passed"]==3 and result["failed"]==0
    assert len(snapshots)==6 and len(called)==3
    assert all(all(kwargs[name] is True for name in (
        "reuse_derived_connections","reuse_producer_connections",
        "reuse_receiver_connection","reuse_original_connection",
        "diagnose_pressure","cooperative_reader_yield",
    )) for kwargs in called)
    assert result["original_connection_reuse_experimental"]
    assert result["history_gap_free_verified"] is False
    assert result["continuous_market_feed_certified"] is False
    assert result["institutional_phase1_complete"] is False
    assert result["orders_submitted"]==0
    assert all(x["memory_free_after_bytes"]>=1_500_000_000
               for x in result["segments"])


@pytest.mark.asyncio
async def test_host_memory_budget_blocks_public_source(tmp_path,monkeypatch):
    called=[]
    async def no_network(*args,**kwargs):
        called.append(True)
        raise AssertionError("must not connect")
    monkeypatch.setattr(operator,"run",no_network)
    monkeypatch.setattr(operator,"host_snapshot",lambda _root: {
        "available_memory_bytes":1_100_000_000,
        "disk_free_bytes":8_000_000_000,
        "evidence_file_bytes":0,
    })
    root=(tmp_path/"low_memory").resolve()
    root.mkdir()
    report=await operator.matrix(root,frames=1000,max_lag_ms=5000,
                                 resource_guard=True)
    assert not called
    assert report["passed"]==0 and report["failed"]==3
    assert all(x["status"]=="NOT_ATTEMPTED_HOST_RESOURCE_GUARD"
               for x in report["segments"])
    assert report["actual_sources_started"]==0
    assert report["skipped_resource_guard"]==3
    assert report["institutional_phase1_complete"] is False


@pytest.mark.asyncio
async def test_unreviewed_connection_switch_is_rejected_before_io(tmp_path):
    root=(tmp_path/"bad").resolve()
    root.mkdir()
    with pytest.raises(ValueError,match="all reviewed caches"):
        await operator.matrix(root,frames=1000,max_lag_ms=5000,
                              reuse_original_connection=True)
    with pytest.raises(ValueError,match="producer and derived"):
        await operator.matrix(root,frames=1000,max_lag_ms=5000,
                              reuse_receiver_connection=True)


@pytest.mark.asyncio
async def test_matrix_progress_durable_before_next_trial(tmp_path,monkeypatch):
    observations=[]
    async def fake_run(path:Path,**kwargs):
        report=json.loads((path.parent/"matrix_report.json").read_text())
        observations.append((report["status"],report["completed_attempts"]))
        path.mkdir()
        return accepted(kwargs["frames"])
    monkeypatch.setattr(operator,"run",fake_run)
    root=(tmp_path/"progress").resolve()
    root.mkdir()
    report=await operator.matrix(root,frames=1000,max_lag_ms=5000)
    assert observations==[
        ("IN_PROGRESS_NOT_CERTIFIED",0),
        ("IN_PROGRESS_NOT_CERTIFIED",1),
        ("IN_PROGRESS_NOT_CERTIFIED",2),
    ]
    assert report["actual_sources_started"]==3
    assert json.loads((root/"matrix_report.json").read_text())["passed"]==3


@pytest.mark.asyncio
async def test_post_source_resource_denial_cannot_be_passed(tmp_path,monkeypatch):
    async def fake_run(path:Path,**kwargs):
        path.mkdir()
        return accepted(kwargs["frames"])
    samples=[2_000_000_000,1_050_000_000]
    def host(_root):
        return {
            "available_memory_bytes":samples.pop(0),
            "disk_free_bytes":8_000_000_000,
            "evidence_file_bytes":10_000_000,
        }
    monkeypatch.setattr(operator,"run",fake_run)
    monkeypatch.setattr(operator,"host_snapshot",host)
    root=(tmp_path/"post").resolve()
    root.mkdir()
    result=await operator.matrix(root,frames=1000,max_lag_ms=5000,
                                 resource_guard=True)
    assert result["passed"]==0
    assert result["actual_sources_started"]==1
    assert result["segments"][0]["status"]=="FAILED_POST_SESSION_RESOURCE_GUARD"
    assert result["segments"][1]["status"]=="NOT_ATTEMPTED_HOST_RESOURCE_GUARD"
    assert result["segments"][2]["status"]=="NOT_ATTEMPTED_HOST_RESOURCE_GUARD"


@pytest.mark.asyncio
async def test_cooperative_reader_requires_full_research_profile(tmp_path):
    root=(tmp_path/"bad_fairness").resolve()
    root.mkdir()
    with pytest.raises(ValueError,match="reader fairness"):
        await operator.matrix(root,frames=1000,max_lag_ms=5000,
                              cooperative_reader_yield=True)
