"""Committed-frame incremental Spot shadow tests: offline, no live network."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

import pytest

from core.mode import QuantMode
from infrastructure.spot_depth_sync import DepthSyncError
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportError,
)
from infrastructure.spot_incremental_shadow import (
    IncrementalSpotShadowValidator, IncrementalSpotIntegrityError,
)


WS = "wss://data-stream.binance.vision/stream"
REST = "https://data-api.binance.vision/api/v3/depth"


def _raw(frame):
    name = ("btcusdt@aggTrade" if frame["e"] == "aggTrade"
            else "btcusdt@depth@100ms")
    return json.dumps({"stream":name,"data":frame}).encode("utf-8")


def _trade(agg, ms):
    return {
        "e":"aggTrade", "E":ms, "T":ms-10, "s":"BTCUSDT",
        "a":agg, "f":agg, "l":agg, "p":"101.25",
        "q":"0.2", "m":False,
    }


def _depth(first, final, ms):
    return {
        "e":"depthUpdate","E":ms,"s":"BTCUSDT",
        "U":first,"u":final,"b":[["100","4"]],"a":[],
    }


def _journal(tmp_path):
    return OfflineSpotCaptureJournal(
        tmp_path/"source.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )


async def _capture(
    journal, now, frames, *,
    session="shadow_01", callback=None,
    capacity=128,
):
    ticks = iter(range(1,1000))
    adapter = OptInSpotIngressAdapter(
        journal,symbol="BTCUSDT",clock_ms=lambda:now+next(ticks),
        queue_capacity=capacity,
    )
    async def source():
        for frame in frames:
            yield _raw(frame)
            await asyncio.sleep(0)
    async def snapshot_fetch(symbol, limit):
        assert symbol == "BTCUSDT" and limit == 1000
        await asyncio.sleep(0)
        return json.dumps({
            "lastUpdateId":100,
            "bids":[["100","3"]],
            "asks":[["102","5"]],
        }).encode("utf-8")
    return await adapter.capture_open_stream(
        source(),snapshot_fetch,
        session_id=session,source="offline_fixture",
        ws_endpoint=WS,rest_endpoint=REST,
        max_frames=len(frames),timeout_s=5,
        on_persisted=callback,
    )


@pytest.mark.asyncio
async def test_incremental_shadow_observes_only_post_commit_frames(tmp_path):
    now=time.time_ns()//1_000_000
    journal=_journal(tmp_path)
    shadow=IncrementalSpotShadowValidator(
        journal,session_id="shadow_01",source="offline_fixture",
    )
    result=await _capture(
        journal,now,
        [_depth(100,102,now),_trade(500,now),_trade(501,now)],
        callback=shadow.observe,
    )
    status=shadow.finish(result)
    assert status.verified_original_records == 4
    assert status.verified_agg_trades == 2
    assert status.verified_depth_updates == 1
    assert status.verified_rest_snapshots == 1
    assert status.book_status == "SYNCED"
    assert status.book_last_update_id == result.last_update_id == 102
    assert status.publisher_events == 0
    assert status.orders_submitted == 0
    assert status.first_trade_baseline_only
    assert not status.complete_exchange_history


@pytest.mark.asyncio
async def test_trade_gap_after_commit_poisoned_without_subscriber_publish(tmp_path):
    now=time.time_ns()//1_000_000
    journal=_journal(tmp_path)
    shadow=IncrementalSpotShadowValidator(
        journal,session_id="shadow_01",source="offline_fixture",
    )
    with pytest.raises(IncrementalSpotIntegrityError,match="trade sequence gap"):
        await _capture(
            journal,now,
            [_depth(100,102,now),_trade(500,now),_trade(502,now)],
            callback=shadow.observe,
        )
    assert shadow._failed
    with journal._connect() as conn:
        row=conn.execute("""
            SELECT closed_at_ms FROM offline_spot_capture_sessions
            WHERE session_id='shadow_01'
        """).fetchone()
    assert row["closed_at_ms"] is not None
    assert shadow.status().publisher_events == 0


@pytest.mark.asyncio
async def test_depth_gap_after_snapshot_requires_reconnect(tmp_path):
    now=time.time_ns()//1_000_000
    journal=_journal(tmp_path)
    shadow=IncrementalSpotShadowValidator(
        journal,session_id="shadow_01",source="offline_fixture",
    )
    async def stream():
        yield _raw(_depth(100,102,now))
        await asyncio.sleep(0.02)
        yield _raw(_depth(110,111,now))
        yield _raw(_trade(500,now))
    async def rest(symbol, limit):
        return json.dumps({
            "lastUpdateId":100,
            "bids":[["100","3"]],"asks":[["102","4"]],
        }).encode()
    adapter=OptInSpotIngressAdapter(journal,symbol="BTCUSDT")
    with pytest.raises(IncrementalSpotIntegrityError,match="depth update gap"):
        await adapter.capture_open_stream(
            stream(),rest,session_id="shadow_01",
            source="offline_fixture",max_frames=3,timeout_s=5,
            on_persisted=shadow.observe,
        )
    assert shadow._failed
    assert shadow.status().publisher_events == 0


@pytest.mark.asyncio
async def test_mismatched_committed_callback_is_rejected(tmp_path):
    now=time.time_ns()//1_000_000
    journal=_journal(tmp_path)
    shadow=IncrementalSpotShadowValidator(
        journal,session_id="shadow_01",source="offline_fixture",
    )
    async def tamper(record):
        modified=replace(record,raw=b'{"attacker":true}')
        await shadow.observe(modified)
    with pytest.raises(IncrementalSpotIntegrityError,match="differs from committed"):
        await _capture(
            journal,now,[_depth(100,102,now),_trade(500,now)],
            callback=tamper,
        )
    assert shadow._failed


@pytest.mark.asyncio
async def test_slow_post_commit_callback_overflows_bounded_writer_fifo(tmp_path):
    now=time.time_ns()//1_000_000
    journal=_journal(tmp_path)
    async def blocked(_record):
        await asyncio.sleep(0.15)
    frames=[_depth(100,102,now)]+[_trade(500+i,now) for i in range(30)]
    with pytest.raises(SpotTransportError,match="backpressure"):
        await _capture(
            journal,now,frames,callback=blocked,
            capacity=2,
        )
    with journal._connect() as conn:
        row=conn.execute("""
            SELECT closed_at_ms FROM offline_spot_capture_sessions
            WHERE session_id='shadow_01'
        """).fetchone()
    assert row["closed_at_ms"] is not None
