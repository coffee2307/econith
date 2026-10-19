"""Single-socket duration research: offline-only fixture tests, no public requests."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time

import pytest

from ci.phase1_single_socket_duration_trial import DurationStream,run_trial
from infrastructure.spot_forward_owner import ForwardOwnershipDenied,exclusive_forward_owner
from infrastructure.spot_public_capture_adapter import SpotTransportError
from ci.phase1_single_socket_duration_audit import inspect


def _frame(i, *, depth=False):
    now=time.time_ns()//1_000_000
    if depth:
        item={"e":"depthUpdate","E":now,"s":"BTCUSDT",
              "U":(100 if i==1 else 103),"u":(102 if i==1 else 104),"b":[["100","3"]],"a":[]}
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
    assert result["verdict"]=="PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY",json.dumps(result,sort_keys=True)
    assert result["duration_target_reached"]
    assert result["single_socket_session_started"]
    assert result["snapshot_captures"]==1
    assert result["websocket_frames"]>=2
    assert result["orders_submitted"]==0
    assert result["exclusive_os_owner_fence_held"] is True
    assert result["terminal_source_silence_s"]<=5.0
    assert not (path/".forward_producer.lock").exists()
    proof=inspect(path)
    assert proof["verdict"]=="PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    assert proof["originals"]==proof["capture_intents"]==result["websocket_frames"]+1
    assert proof["independently_reverified_exchange_completeness"] is False
    assert proof["orphan_owner_marker_present"] is False
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
async def test_deadline_with_no_last_frame_is_not_market_liveness():
    async def silent():
        await asyncio.sleep(1)
        yield b"late"
    stream=DurationStream(silent(),duration_s=0.10)
    with pytest.raises(SpotTransportError,match="recent source frame"):
        async for _ in stream:
            pass
    assert stream.target_reached is False


@pytest.mark.asyncio
async def test_stale_last_frame_near_deadline_is_not_success():
    async def silent_after_frame():
        yield b"one"
        await asyncio.sleep(1)
        yield b"late"
    stream=DurationStream(silent_after_frame(),duration_s=0.10,
                          max_terminal_silence_s=0.02)
    with pytest.raises(SpotTransportError) as err:
        async for _ in stream:
            pass
    assert err.value.reason=="WS_SILENT_AT_DEADLINE"
    assert stream.target_reached is False


@pytest.mark.asyncio
async def test_live_capture_owner_denies_concurrent_reader(tmp_path):
    root=tmp_path/"single_owner"
    task=asyncio.create_task(run_trial(
        root,allow_public_market_data=True,duration_s=0.22,_fixture=True,
        lease_factory=fake_lease(),clock_preflight=preflight,
    ))
    marker=root/".forward_producer.lock"
    for _ in range(70):
        if marker.exists():
            break
        await asyncio.sleep(.005)
    assert marker.is_file()
    with pytest.raises(ForwardOwnershipDenied,match="orphaned"):
        with exclusive_forward_owner(root,role="STAGED_RECOVERY"):
            pytest.fail("second writer must not acquire a running capture")
    result=await task
    assert result["verdict"]=="PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY",result
    assert not marker.exists()
    assert inspect(root)["verdict"]=="PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"


@pytest.mark.skipif(sys.platform!="linux",reason="OS hard-exit CI uses Linux")
def test_real_hard_exit_leaves_orphan_proof_blocks_unreviewed_takeover(tmp_path):
    root=(tmp_path/"hard_kill").resolve()
    script=r'''
import asyncio,json,os,sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from ci.phase1_single_socket_duration_trial import run_trial
async def preflight(**kwargs):
    return SimpleNamespace(round_trip_ms=1,measured_offset_ms=0)
@asynccontextmanager
async def lease(*,allow_public_market_data):
    assert allow_public_market_data
    class One:
        @property
        def stream(self):
            async def generator():
                now=__import__("time").time_ns()//1_000_000
                depth={"e":"depthUpdate","E":now,"s":"BTCUSDT","U":100,
                       "u":102,"b":[["100","3"]],"a":[]}
                yield json.dumps({"stream":"btcusdt@depth@100ms","data":depth}).encode()
                await asyncio.sleep(.12)
                os._exit(47)
            return generator()
        async def snapshot(self,symbol,limit):
            return json.dumps({"lastUpdateId":100,"bids":[["100","2"]],
                               "asks":[["102","2"]]}).encode()
    yield One()
asyncio.run(run_trial(
    Path(sys.argv[1]),allow_public_market_data=True,
    duration_s=.40,_fixture=True,lease_factory=lease,
    clock_preflight=preflight,
))
'''
    proc=subprocess.run([sys.executable,"-c",script,str(root)],
                        text=True,capture_output=True,timeout=25,
                        cwd=str(Path(__file__).resolve().parents[1]))
    assert proc.returncode==47,proc.stderr[-1200:]
    marker=root/".forward_producer.lock"
    assert marker.is_file()
    with pytest.raises(ForwardOwnershipDenied,match="orphaned"):
        with exclusive_forward_owner(root,role="CAPTURE"):
            pytest.fail("orphan marker cannot be silently stolen")
    assert not (root/"aggregate_report.json").exists()
    with sqlite3.connect((root/"original.sqlite").resolve().as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0]>=1
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_sessions WHERE closed_at_ms IS NULL").fetchone()[0]==1
    assert marker.is_file()
