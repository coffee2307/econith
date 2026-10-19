"""Bounded causal source/projector pressure telemetry is never source evidence."""
from __future__ import annotations

import json

import pytest

from ci.phase1_offline_burst_probe import _source, _snapshot
from infrastructure.spot_forward_verified_research import (
    ForwardSpotResearchError, observe_forward_spot_research,
)
from infrastructure.spot_spool_pressure_diagnostics import BoundedSpoolPressure


def test_pressure_windows_show_ingress_vs_projection_and_truncate(tmp_path):
    t=[0]
    cpu=[0]
    memory=[2_000_000_000]
    sample=BoundedSpoolPressure(
        tmp_path/"spool_pressure.json",
        clock_ns=lambda:t[0],
        cpu_clock_ns=lambda:cpu[0],
        memory_probe=lambda:memory[0],
    )
    sample.raw(1)
    sample.raw(2)
    sample.fifo(127,128)
    sample.receiver_ack(501.25)
    t[0]=5_100_000_000
    cpu[0]=400_000_000
    sample.projected()
    sample.published(100)
    sample.checkpoint()
    t[0]=10_100_000_000
    memory[0]=1_200_000_000
    sample.raw(2)
    sample.write(source_complete=False)
    report=json.loads((tmp_path/"spool_pressure.json").read_text())
    assert report["source_complete"] is False
    assert report["counters"]=={
        "raw_committed":3, "cursor_advanced":1,
        "eventbus_published":1, "checkpoints":1,
    }
    assert report["windows"][0]["raw_originals_per_s"]==0.4
    assert report["windows"][0]["max_ingress_fifo_depth"]==127
    assert report["windows"][0]["ingress_fifo_90pct_observations"]==1
    assert report["windows"][0]["max_receiver_ack_ms"]==502
    assert report["windows"][0]["receiver_ack_over_500ms"]==1
    assert report["windows"][0]["cursor_originals_per_s"]==0
    assert report["windows"][1]["cursor_originals_per_s"]==0.2
    assert report["windows"][1]["max_admission_age_ms"]==100
    assert report["windows"][2]["host_available_memory_bytes"]==1_200_000_000
    assert report["event_payloads_or_identifiers_recorded"] is False
    assert report["source_coverage_certified"] is False
    with pytest.raises(FileExistsError):
        sample.write(source_complete=False)


@pytest.mark.parametrize("bad",[-1,1.2,True])
def test_invalid_raw_backlog_is_rejected(tmp_path,bad):
    pressure=BoundedSpoolPressure(tmp_path/"spool_pressure.json")
    with pytest.raises(ValueError):
        pressure.raw(bad)


@pytest.mark.asyncio
async def test_in_session_spool_pressure_matches_durable_ledger(tmp_path):
    root=(tmp_path/"good").resolve()
    result=await observe_forward_spot_research(
        output_dir=root,session_id="rec_pressure_success",
        stream=_source(30,60),snapshot_fetch=_snapshot,
        max_frames=30,timeout_s=30,persistent_receiver=True,
        spooled_in_session=True,experiment_reuse_derived_connections=True,
        diagnose_pressure=True,
    )
    pressure=json.loads((root/"spool_pressure.json").read_text())
    latency=json.loads((root/"stage_latency.json").read_text())
    assert pressure["source_complete"] is True
    assert pressure["counters"]["raw_committed"]==result.original_records
    assert pressure["counters"]["cursor_advanced"]==result.original_records
    assert pressure["counters"]["eventbus_published"]==result.receiver_receipts
    assert pressure["counters"]["checkpoints"]==result.forward_checkpoints
    assert sum(x["receiver_ack_observations"] for x in pressure["windows"])==result.receiver_receipts
    assert max(x["max_ingress_fifo_depth"] for x in pressure["windows"])<=result.queue_high_watermark
    assert latency["stages"]["receiver_hmac_ack"]["samples"]==result.receiver_receipts
    assert latency["stages"]["source_sqlite_commit"]["samples"]==result.original_records
    assert result.orders_submitted==0


@pytest.mark.asyncio
async def test_spool_failed_ingress_preserves_incomplete_pressure(tmp_path):
    root=(tmp_path/"bad").resolve()
    with pytest.raises(ForwardSpotResearchError,match="pending capacity exceeded"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_pressure_failure",
            stream=_source(50,0),snapshot_fetch=_snapshot,
            max_frames=50,timeout_s=30,persistent_receiver=True,
            spooled_in_session=True,experiment_reuse_derived_connections=True,
            diagnose_pressure=True,max_pending_originals=1,
        )
    result=json.loads((root/"spool_pressure.json").read_text())
    assert result["source_complete"] is False
    assert result["counters"]["raw_committed"]>=2
    assert (root/"stage_latency.json").exists()


@pytest.mark.asyncio
async def test_pressure_flag_requires_explicit_spool(tmp_path):
    root=(tmp_path/"refused").resolve()
    with pytest.raises(PermissionError,match="require opt-in spool"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_unapproved_pressure",
            stream=_source(30,60),snapshot_fetch=_snapshot,
            max_frames=30,timeout_s=30,persistent_receiver=True,
            diagnose_pressure=True,
        )
    assert not root.exists()


def test_pressure_rejects_invalid_fifo_and_hmac_latency(tmp_path):
    p=BoundedSpoolPressure(tmp_path/"spool_pressure.json")
    for depth,capacity in [(-1,128),(129,128),(1,0),(True,128)]:
        with pytest.raises(ValueError):
            p.fifo(depth,capacity)
    for value in [-1.0,float("nan"),float("inf"),True]:
        with pytest.raises(ValueError):
            p.receiver_ack(value)
