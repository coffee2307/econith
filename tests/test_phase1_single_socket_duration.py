"""Single-socket duration research: offline-only fixture tests, no public requests."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time

import pytest

from ci.phase1_single_socket_duration_trial import DurationStream,run_trial
from ci.phase1_single_socket_duration_audit import inspect


def _frame(i, *, depth=False):
    now=time.time_ns()//1_000_000
    if depth:
        item={"e":"depthUpdate","E":now,"s":"BTCUSDT",
              "U":100+i,"u":100+i,"b":[["100","3"]],"a":[]}
        stream="btcusdt@depth@100ms"
    else:
        item={"e":"aggTrade","E":now,"T":now,"s":"BTCUSDT",
              "a":i,"f":i,"l":i,"p":"100","q":"0.02","m":False}
        stream="btcusdt@aggTrade"
    return json.dumps({"stream":stream,"data":item},separators=(",",":")).encode()


def _snapshot():
    return json.dumps({"lastUpdateId":100,"bids":[["100","2"],["99","3"]],
                       "asks":[["102","2"],["103","1"]]}).encode()


async def preflight(**kwargs):
    assert kwargs=={"max_abs_offset_ms":3000,"max_rtt_ms":2000}
    return SimpleNamespace(round_trip_ms=10,measured_offset_ms=0)


def fake_lease(*, end_early=False, bad_frame=False, rest_fails=False):
    @asynccontextmanager
    async def factory(*,allow_public_market_data):
        assert allow_public_market_data is True
        class Lease:
            @property
            def stream(self):
                async def source():
                    i=0
                    while True:
                        await asyncio.sleep(0.015)
                        i+=1
                        if end_early and i==3:
                            return
                        if bad_frame and i==3:
                            yield b'{"data":"oops"}'
                        elif i==1:
                            yield _frame(1,depth=True)
                        elif i==2:
                            yield _frame(2,depth=True)
                        else:
                            yield _frame(i,depth=False)
                return source()
            async def snapshot(self,symbol,limit):
                assert symbol=="BTCUSDT" and limit==1000
                if rest_fails:
                    raise OSError("injected rest outage")
                await asyncio.sleep(0)
                return _snapshot()
        yield Lease()
    return factory


@pytest.mark.asyncio
async def test_one_live_socket_duration_fixture_passes_with_raw_intent_independent_audit(tmp_path):
    path=tmp_path/"capture"
    result=await run_trial(
        path,allow_public_market_data=True,duration_s=0.15,_fixture=True,
        lease_factory=fake_lease(),clock_preflight=preflight,
    )
    assert result["verdict"]=="PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY",result
    assert result["duration_target_reached"]
    assert result["single_socket_session_started"]
    assert result["snapshot_captures"]==1
    assert result["websocket_frames"]>=2
    assert result["orders_submitted"]==0
    proof=inspect(path)
    assert proof["verdict"]=="PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    assert proof["originals"]==proof["capture_intents"]==result["websocket_frames"]+1
    assert proof["independently_reverified_exchange_completeness"] is False
    with pytest.raises(ValueError):
        await run_trial(path,allow_public_market_data=True,
                        duration_s=0.15,_fixture=True,lease_factory=fake_lease(),
                        clock_preflight=preflight)
    raw=path/"original.sqlite"
    assert raw.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("variant",["early","bad","rest"])
async def test_missing_network_evidence_quarantines_not_pass(tmp_path,variant):
    factory=fake_lease(end_early=variant=="early",
                       bad_frame=variant=="bad",
                       rest_fails=variant=="rest")
    report=await run_trial(tmp_path/"fail",allow_public_market_data=True,
                           duration_s=0.15,_fixture=True,
                           lease_factory=factory,clock_preflight=preflight)
    assert report["verdict"]=="FAILED_OR_QUARANTINED",report
    assert report["orders_submitted"]==0
    assert report["continuous_source_connection_observed"] is False
    assert inspect(tmp_path/"fail")["verdict"]=="NOT_ACCEPTED"


@pytest.mark.asyncio
async def test_explicit_opt_in_and_real_duration_limits_fail_before_network(tmp_path):
    with pytest.raises(PermissionError):
        await run_trial(tmp_path/"no",allow_public_market_data=False)
    assert not (tmp_path/"no").exists()
    with pytest.raises(ValueError):
        await run_trial(tmp_path/"short",allow_public_market_data=True,
                        duration_s=0.15,lease_factory=fake_lease(),clock_preflight=preflight)
    assert not (tmp_path/"short").exists()


@pytest.mark.asyncio
async def test_duration_stream_early_eof_rejected():
    async def source():
        yield b"fixture"
    stream=DurationStream(source(),duration_s=0.1)
    with pytest.raises(Exception,match="EOF"):
        async for _ in stream:
            pass
    assert stream.early_eof and not stream.target_reached


@pytest.mark.asyncio
async def test_duration_stream_deadline_while_idle_counts_as_observation():
    async def source():
        await asyncio.sleep(1)
        yield b"late"
    stream=DurationStream(source(),duration_s=0.10)
    assert [x async for x in stream]==[]
    assert stream.target_reached
    assert stream.elapsed_s>=0.09
