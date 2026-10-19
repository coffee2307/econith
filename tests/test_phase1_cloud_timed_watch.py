"""Cloud 10-minute research watch opt-in flags and evidence coverage are bounded."""
from __future__ import annotations

import pytest
from ci.phase1_public_spool_timed_watch import timed_watch


class FakeClock:
    now=0.0
    def __call__(self):
        return self.now


def healthy_metrics(_root):
    return {
        "available_memory_bytes":5_000_000_000,
        "disk_free_bytes":10_000_000_000,
        "evidence_file_bytes":12345,
    }


def accepted(frames: int)->dict:
    return {
        "verdict":"PASSED_FINITE_PUBLIC_IN_SESSION_SPOOL_RESEARCH_ONLY",
        "mode":"SIMULATION",
        "derived_connection_reuse_experimental":True,
        "original_records":frames+1,
        "spool_original_cursor":frames+1,
        "receiver_receipts":frames-10,
        "forward_checkpoints":frames-10,
        "projector_age_samples":frames-10,
        "projector_p95_admission_age_upper_ms":149,
        "projector_p99_admission_age_upper_ms":199,
        "projector_min_headroom_ms":4750,
        "max_projector_lag_ms":250,
        "queue_high_watermark":12,
        "max_spool_pending_originals":20,
        "publication_in_session":True,
        "session_complete":True,
        "all_receipts_independent":True,
        "execution_started":False,
        "orders_submitted":0,
        "history_continuity_verified":False,
        "continuous_live_market":False,
    }


@pytest.mark.asyncio
async def test_cloud_full_cache_optins_never_imply_continuity(tmp_path):
    clock=FakeClock()
    calls=[]
    async def fake_trial(path,**kwargs):
        calls.append(kwargs)
        path.mkdir()
        clock.now+=90
        return accepted(kwargs["frames"])
    root=(tmp_path/"cloud_watch").resolve()
    report=await timed_watch(
        root,clock=clock,metrics=healthy_metrics,single_trial=fake_trial,
        diagnose_pressure=True,reuse_producer_connections=True,
        reuse_receiver_connection=True,reuse_original_connection=True,
        cooperative_reader_yield=True,
    )
    assert report["target_reached"]
    assert report["passed_segments"]==7
    assert report["segment_boundaries_without_verified_coverage"]==6
    assert report["original_connection_reuse_experimental"] is True
    assert all(all(c[k] is True for k in (
        "reuse_derived_connections","reuse_producer_connections",
        "reuse_receiver_connection","reuse_original_connection",
        "diagnose_pressure","cooperative_reader_yield",
    )) for c in calls)
    assert report["continuous_live_market"] is False
    assert report["history_continuity_verified"] is False
    assert report["institutional_phase1_complete"] is False


@pytest.mark.asyncio
async def test_cloud_original_only_or_receiver_only_refused_before_directory(tmp_path):
    root=(tmp_path/"denied").resolve()
    with pytest.raises(ValueError,match="original cache"):
        await timed_watch(root,reuse_original_connection=True)
    assert not root.exists()
    with pytest.raises(ValueError,match="receiver cache"):
        await timed_watch(root,reuse_receiver_connection=True)
    assert not root.exists()


@pytest.mark.asyncio
async def test_cooperative_reader_not_permitted_without_full_caches(tmp_path):
    root=(tmp_path/"guard_fairness").resolve()
    with pytest.raises(ValueError,match="cooperative reader"):
        await timed_watch(root,cooperative_reader_yield=True)
    assert not root.exists()
