"""Reader fairness under already-buffered bursts: safety and strict source provenance."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter, SpotTransportError
from infrastructure.spot_spool_pressure_diagnostics import BoundedSpoolPressure


def _raw(event:dict,kind:str)->bytes:
    return json.dumps(
        {"stream":f"btcusdt@{kind}","data":event},
        separators=(",",":"),sort_keys=True,
    ).encode()


async def _burst(frames:int):
    now=time.time_ns()//1_000_000
    yield _raw({"e":"depthUpdate","s":"BTCUSDT","E":now,
                "U":100,"u":102,"b":[["100","4"]],"a":[]},
               "depth@100ms")
    for i in range(1,frames):
        now=time.time_ns()//1_000_000
        yield _raw({"e":"aggTrade","s":"BTCUSDT","E":now,
                    "T":now-10,"a":2000+i,"f":2000+i,"l":2000+i,
                    "p":"101","q":"0.02","m":False},"aggTrade")


async def _rest(*args):
    await asyncio.sleep(0)
    return json.dumps({
        "lastUpdateId":100,"bids":[["100","3"]],"asks":[["102","5"]],
    }).encode()


@pytest.mark.asyncio
async def test_prebuffered_burst_cooperative_reader_prevents_spurious_starvation(tmp_path):
    results=[]
    for flag in (False,True):
        root=(tmp_path/("yield" if flag else "old")).resolve()
        root.mkdir()
        journal=OfflineSpotCaptureJournal(
            root/"original.sqlite",
            mode_provider=lambda:QuantMode.SIMULATION,
            forward_capture_audit=True,
            reuse_connection=True,
        )
        pressure=BoundedSpoolPressure(root/"spool_pressure.json")
        adapter=OptInSpotIngressAdapter(
            journal,symbol="BTCUSDT",queue_capacity=8,
            on_queue_depth=pressure.fifo,
            on_queue_event=pressure.fifo_event,
            cooperative_reader_yield=flag,
        )
        error=None
        try:
            result=await adapter.capture_open_stream(
                _burst(180),_rest,session_id="rec_burst_fairness",
                max_frames=180,timeout_s=30,
                require_frame_limit=True,
            )
        except SpotTransportError as exc:
            error=exc
        finally:
            pressure.write(source_complete=(error is None))
            journal.close()
        diag=json.loads((root/"spool_pressure.json").read_text())
        with sqlite3.connect(root/"original.sqlite") as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
            originals=db.execute(
                "SELECT COUNT(*) FROM offline_spot_capture_records"
            ).fetchone()[0]
            intents=db.execute(
                "SELECT COUNT(*) FROM offline_forward_capture_intents"
            ).fetchone()[0]
            missing=db.execute(
                "SELECT COUNT(*) FROM offline_spot_capture_records r "
                "LEFT JOIN offline_forward_capture_intents i "
                "ON i.capture_id=r.capture_id WHERE i.capture_id IS NULL "
                "OR i.sha256<>r.sha256"
            ).fetchone()[0]
            assert originals==intents and missing==0
            closed=db.execute(
                "SELECT closed_at_ms FROM offline_spot_capture_sessions"
            ).fetchone()[0]
            assert closed is not None
        results.append((error,result if error is None else None,diag,originals))
    first,second=results
    assert first[0] is not None and first[0].reason=="QUEUE_BACKPRESSURE"
    assert first[2]["subsecond_fifo_totals"]["overflow"]==1
    assert first[2]["source_complete"] is False
    assert second[0] is None
    assert second[1].websocket_frames==180
    assert second[1].snapshot_captures==1
    assert second[1].depth_status=="SYNCED"
    assert second[3]==181
    assert second[2]["subsecond_fifo_totals"]["enqueued"]==181
    assert second[2]["subsecond_fifo_totals"]["overflow"]==0
    assert second[2]["source_complete"] is True
    assert second[1].queue_high_watermark<8


@pytest.mark.asyncio
async def test_cooperative_reader_still_quarantines_genuinely_slow_writer(tmp_path):
    root=(tmp_path/"slow").resolve()
    root.mkdir()
    journal=OfflineSpotCaptureJournal(
        root/"original.sqlite",
        mode_provider=lambda:QuantMode.SIMULATION,
        forward_capture_audit=True,reuse_connection=True,
    )
    pressure=BoundedSpoolPressure(root/"spool_pressure.json")
    adapter=OptInSpotIngressAdapter(
        journal,symbol="BTCUSDT",queue_capacity=8,
        on_queue_event=pressure.fifo_event,
        cooperative_reader_yield=True,
    )

    async def slow_verifier(_record):
        await asyncio.sleep(0.15)

    with pytest.raises(SpotTransportError) as exc:
        await adapter.capture_open_stream(
            _burst(180),_rest,session_id="rec_burst_slow",
            max_frames=180,timeout_s=20,require_frame_limit=True,
            on_persisted=slow_verifier,
        )
    assert exc.value.reason=="QUEUE_BACKPRESSURE"
    pressure.write(source_complete=False)
    # Both WS and concurrent REST snapshot can independently encounter a full FIFO.
    assert pressure.summary(source_complete=False)["subsecond_fifo_totals"]["overflow"]>=1
    with sqlite3.connect(root/"original.sqlite") as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute(
            "SELECT closed_at_ms FROM offline_spot_capture_sessions"
        ).fetchone()[0] is not None
    journal.close()


@pytest.mark.parametrize("bad",[None,1,0,"true"])
def test_invalid_cooperative_flag_rejected_before_io(tmp_path,bad):
    journal=OfflineSpotCaptureJournal(
        tmp_path/"journal.sqlite",mode_provider=lambda:QuantMode.SIMULATION,
    )
    with pytest.raises(ValueError,match="explicit bool"):
        OptInSpotIngressAdapter(
            journal,symbol="BTCUSDT",cooperative_reader_yield=bad,
        )
