"""Fixed-footprint Phase 1 admission-age histograms are diagnostic only."""
from __future__ import annotations

import json

import pytest

from infrastructure.spot_projector_lag_histogram import BoundedProjectorLag
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from ci.phase1_offline_burst_probe import _source, _snapshot


def test_histogram_exact_count_and_conservative_percentile_upper_bounds(tmp_path):
    report = BoundedProjectorLag(tmp_path/"projector_lag.json", age_budget_ms=5000)
    for age in [0, 50, 100, 200, 200, 500, 800, 1200, 3200, 4790]:
        report.admit(age)
    for pending in [0, 1, 1, 17, 65, 127]:
        report.pending(pending)
    stats=report.summary(source_complete=True)
    assert stats["age_samples"]==10
    assert stats["p50_age_upper_ms"]==249
    assert stats["p95_age_upper_ms"]==4799
    assert stats["p99_age_upper_ms"]==4799
    assert stats["max_age_exact_ms"]==4790
    assert stats["minimum_observed_headroom_ms"]==210
    assert stats["max_pending_originals"]==127
    assert len(stats["age_buckets"])==202
    assert len(stats["pending_buckets_64plus"])==65
    assert stats["raw_events_or_identifiers_recorded"] is False
    report.write(source_complete=True)
    disk=json.loads((tmp_path/"projector_lag.json").read_text())
    assert disk==stats
    with pytest.raises(FileExistsError):
        report.write(source_complete=True)


def test_empty_failed_report_is_explicitly_incomplete(tmp_path):
    p=BoundedProjectorLag(tmp_path/"projector_lag.json", age_budget_ms=5000)
    stats=p.summary(source_complete=False)
    assert stats["source_complete"] is False
    assert stats["p95_age_upper_ms"] is None
    assert stats["age_samples"]==0
    p.write(source_complete=False)


@pytest.mark.parametrize("age,pending", [(-1,None), (None,-1), (3.5,None), (None,1.5)])
def test_invalid_samples_rejected(tmp_path,age,pending):
    p=BoundedProjectorLag(tmp_path/"projector_lag.json", age_budget_ms=5000)
    with pytest.raises(ValueError):
        if age is not None:
            p.admit(age)
        else:
            p.pending(pending)


@pytest.mark.asyncio
async def test_optin_worker_histogram_matches_published_receipts(tmp_path):
    root=(tmp_path/"in_session").resolve()
    status=await observe_forward_spot_research(
        output_dir=root, session_id="rec_histogram_fixture",
        stream=_source(30,60),snapshot_fetch=_snapshot,
        max_frames=30,timeout_s=30,
        persistent_receiver=True,spooled_in_session=True,
        max_projector_lag_ms=5000,
        experiment_reuse_derived_connections=True,
    )
    report=json.loads((root/"projector_lag.json").read_text())
    assert report["source_complete"] is True
    assert report["age_samples"]==status.receiver_receipts
    assert status.projector_age_samples==status.receiver_receipts
    assert status.projector_p95_admission_age_upper_ms==report["p95_age_upper_ms"]
    assert status.projector_p99_admission_age_upper_ms==report["p99_age_upper_ms"]
    assert status.projector_min_headroom_ms==report["minimum_observed_headroom_ms"]
    assert report["max_pending_originals"]==status.max_spool_pending_originals
    assert not (root/"projector_lag.json").read_text().find("rec_histogram_fixture")>=0


@pytest.mark.asyncio
async def test_overload_quarantine_keeps_incomplete_histogram(tmp_path):
    root=(tmp_path/"quarantined").resolve()
    with pytest.raises(Exception):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_histogram_failure",
            stream=_source(50,0),snapshot_fetch=_snapshot,
            max_frames=50,timeout_s=30,persistent_receiver=True,
            spooled_in_session=True,max_pending_originals=1,
            experiment_reuse_derived_connections=True,
        )
    report=json.loads((root/"projector_lag.json").read_text())
    assert report["source_complete"] is False
    assert report["raw_events_or_identifiers_recorded"] is False
