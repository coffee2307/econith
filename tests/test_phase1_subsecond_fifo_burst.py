"""250ms ingress transport diagnostics: deterministic burst and fail-closed proof.

No Binance network, no orders, no market payloads in diagnostic outputs.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter, SpotTransportError
from infrastructure.spot_spool_pressure_diagnostics import (
    BoundedSpoolPressure, SUBSECOND_WINDOW_MS, MAX_SUBSECOND_WINDOWS,
)


def test_subsecond_fifo_counts_precise_burst_and_writer_cost(tmp_path):
    current=[0]
    pressure=BoundedSpoolPressure(
        tmp_path/"spool_pressure.json",
        clock_ns=lambda:current[0],
        cpu_clock_ns=lambda:current[0],
        memory_probe=lambda:4_000_000_000,
    )
    pressure.fifo_event("enqueue",1,128,None)
    current[0]=10_000_000
    pressure.fifo_event("enqueue",124,128,None)
    current[0]=30_000_000
    pressure.fifo_event("overflow",128,128,None)
    current[0]=125_000_000
    pressure.fifo_event("dequeue",127,128,None)
    pressure.fifo_event("writer_complete",127,128,11.5)
    current[0]=260_000_000
    pressure.fifo_event("enqueue",2,128,None)
    pressure.write(source_complete=False)
    report=json.loads((tmp_path/"spool_pressure.json").read_text())
    assert report["subsecond_window_ms"]==SUBSECOND_WINDOW_MS==250
    assert report["subsecond_fifo_totals"]=={
        "enqueued":3, "dequeued":1,"writer_completed":1,"overflow":1,
    }
    first,second=report["subsecond_windows"]
    assert first["elapsed_start_ms"]==0
    assert first["enqueued"]==2 and first["overflow"]==1
    assert first["peak_fifo_depth"]==128
    assert first["near_full_observations"]>=3
    assert first["max_writer_service_ms"]==12
    assert second["elapsed_start_ms"]==250 and second["enqueued"]==1
    assert report["event_payloads_or_identifiers_recorded"] is False
    assert report["source_coverage_certified"] is False


def test_subsecond_diagnostics_never_expand_unbounded(tmp_path):
    ticks=[0]
    p=BoundedSpoolPressure(tmp_path/"spool_pressure.json",clock_ns=lambda:ticks[0])
    p.fifo_event("enqueue",1,128,None)
    ticks[0]=900_000_000_000
    p.fifo_event("overflow",128,128,None)
    p.write(source_complete=False)
    diag=json.loads((tmp_path/"spool_pressure.json").read_text())
    assert len(diag["subsecond_windows"])==MAX_SUBSECOND_WINDOWS
    assert diag["subsecond_windows_truncated"] is True
    assert diag["subsecond_fifo_totals"]["overflow"]==1


@pytest.mark.parametrize("action,depth,capacity,duration",[
    ("bad",1,128,None),("enqueue",-1,128,None),
    ("enqueue",129,128,None),("dequeue",0,0,None),
    ("enqueue",1,128,1.0),("writer_complete",1,128,-1),
    ("writer_complete",1,128,float("nan")),
])
def test_invalid_fifo_diagnostic_sample_refused(tmp_path,action,depth,capacity,duration):
    p=BoundedSpoolPressure(tmp_path/"spool_pressure.json")
    with pytest.raises(ValueError):
        p.fifo_event(action,depth,capacity,duration)


@pytest.mark.asyncio
async def test_fast_burst_overflows_without_source_acceptance(tmp_path):
    from tests.test_spot_public_capture_adapter import raw, trade
    root=(tmp_path/"overflow").resolve()
    root.mkdir()
    journal=OfflineSpotCaptureJournal(
        root/"original.sqlite",
        mode_provider=lambda:QuantMode.SIMULATION,
        forward_capture_audit=True,
    )
    pressure=BoundedSpoolPressure(root/"spool_pressure.json")
    adapter=OptInSpotIngressAdapter(
        journal,symbol="BTCUSDT",queue_capacity=8,
        on_queue_depth=pressure.fifo,
        on_queue_event=pressure.fifo_event,
    )

    async def immediate_source():
        for i in range(100,160):
            yield raw(trade(i))

    async def no_rest(*_args):
        raise AssertionError("REST should not run without depth")

    with pytest.raises(SpotTransportError) as exc:
        await adapter.capture_open_stream(
            immediate_source(),no_rest,
            session_id="rec_microburst_overflow",max_frames=60,
            timeout_s=10,require_frame_limit=True,
        )
    assert exc.value.reason=="QUEUE_BACKPRESSURE"
    pressure.write(source_complete=False)
    p=json.loads((root/"spool_pressure.json").read_text())
    assert p["source_complete"] is False
    assert p["subsecond_fifo_totals"]["overflow"]==1
    assert p["subsecond_fifo_totals"]["enqueued"]==8
    assert max(w["peak_fifo_depth"] for w in p["subsecond_windows"])==8
    with sqlite3.connect(root/"original.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0]<=8
        assert db.execute("SELECT closed_at_ms FROM offline_spot_capture_sessions").fetchone()[0] is not None
