"""No live network or orders in timed research watch tests."""
from __future__ import annotations

import json

import pytest

from ci.phase1_public_spool_timed_watch import (
    WatchPolicy, _valid_session, timed_watch,
)


class SyntheticClock:
    def __init__(self):
        self.value=1000.0
    def __call__(self):
        return self.value


def fake_metrics(_root):
    return {
        "available_memory_bytes":4_000_000_000,
        "disk_free_bytes":8_000_000_000,
        "evidence_file_bytes":5_000_000,
    }


def finite_success(frames=1000):
    return {
        "verdict":"PASSED_FINITE_PUBLIC_IN_SESSION_SPOOL_RESEARCH_ONLY",
        "mode":"SIMULATION",
        "derived_connection_reuse_experimental":True,
        "original_records":frames+1,
        "spool_original_cursor":frames+1,
        "receiver_receipts":frames-10,
        "forward_checkpoints":frames-10,
        "projector_age_samples":frames-10,
        "projector_p99_admission_age_upper_ms":1150,
        "projector_p95_admission_age_upper_ms":950,
        "projector_min_headroom_ms":3600,
        "max_projector_lag_ms":1400,
        "queue_high_watermark":32,
        "max_spool_pending_originals":45,
        "publication_in_session":True,
        "session_complete":True,
        "all_receipts_independent":True,
        "execution_started":False,
        "orders_submitted":0,
        "history_continuity_verified":False,
        "continuous_live_market":False,
    }


@pytest.mark.asyncio
async def test_ten_minute_target_never_certifies_intersegment_history(tmp_path):
    clock=SyntheticClock()
    calls=[]
    async def fake_trial(root,**kwargs):
        calls.append(root)
        assert kwargs["reuse_derived_connections"] is True
        clock.value+=90
        root.mkdir()
        return finite_success(kwargs["frames"])
    root=(tmp_path/"watch").resolve()
    result=await timed_watch(root,clock=clock,metrics=fake_metrics,
                             single_trial=fake_trial)
    assert result["target_reached"]
    assert result["passed_segments"]==7
    assert result["segment_boundaries_without_verified_coverage"]==6
    assert result["history_continuity_verified"] is False
    assert result["continuous_live_market"] is False
    assert result["institutional_phase1_complete"] is False
    assert result["verdict"]=="PASSED_FINITE_10MIN_MULTISEGMENT_RESEARCH_WATCH_ONLY"
    assert len(calls)==7
    assert json.loads((root/"watch_report.json").read_text())==result


@pytest.mark.asyncio
async def test_failed_segment_remains_failed_and_no_retry(tmp_path):
    clock=SyntheticClock()
    calls=0
    async def failure_after_one(root,**kwargs):
        nonlocal calls
        calls+=1
        clock.value+=90
        root.mkdir()
        if calls==2:
            raise RuntimeError("simulated stale receipt")
        return finite_success()
    root=(tmp_path/"failed").resolve()
    result=await timed_watch(root,clock=clock,metrics=fake_metrics,
                             single_trial=failure_after_one)
    assert calls==2 and result["passed_segments"]==1
    assert result["failed_or_incomplete_segments"]==1
    assert result["target_reached"] is False
    assert result["verdict"]=="FAILED_OR_QUARANTINED"
    assert result["segments"][1]["status"]=="FAILED_OR_QUARANTINED"
    assert result["segment_boundaries_without_verified_coverage"]==1


@pytest.mark.asyncio
async def test_resource_budget_blocks_network_preflight(tmp_path):
    attempts=[]
    async def unexpected_network(*args,**kwargs):
        attempts.append(True)
        raise AssertionError("source should not open")
    root=(tmp_path/"low_memory").resolve()
    def low_memory(_root):
        return {
            "available_memory_bytes":123,
            "disk_free_bytes":8_000_000_000,
            "evidence_file_bytes":0,
        }
    result=await timed_watch(root,clock=SyntheticClock(),
        metrics=low_memory,single_trial=unexpected_network)
    assert not attempts
    assert result["verdict"]=="FAILED_HOST_RESOURCE_BUDGET"
    assert not result["target_reached"]


def test_invalid_or_fake_source_verdict_not_accepted():
    valid=finite_success()
    assert _valid_session(valid,1000,5000)
    for field,value in [
        ("projector_age_samples",900),
        ("forward_checkpoints",900),
        ("projector_min_headroom_ms",-1),
        ("projector_p99_admission_age_upper_ms",None),
        ("orders_submitted",1),
        ("history_continuity_verified",True),
        ("derived_connection_reuse_experimental",False),
    ]:
        broken={**valid,field:value}
        assert not _valid_session(broken,1000,5000)


def test_watch_policy_rejects_unsafe_resource_budget():
    with pytest.raises(ValueError):
        WatchPolicy(min_watch_s=100).validate()
    with pytest.raises(ValueError):
        WatchPolicy(min_free_disk_bytes=0).validate()
    with pytest.raises(ValueError):
        WatchPolicy(max_watch_s=3600).validate()
