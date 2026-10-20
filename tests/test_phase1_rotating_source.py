"""One background Spot socket reader, multiple sealed originals: offline-only acceptance."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from ci.phase1_rotating_spot_source import run_rotation
from ci.phase1_rotating_source_audit import inspect
from infrastructure.spot_public_capture_adapter import SourceStampedSpotFrame,OptInSpotIngressAdapter
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from core.mode import QuantMode


async def public_clock(**kwargs):
    assert kwargs=={"max_abs_offset_ms":3000,"max_rtt_ms":2000}
    return SimpleNamespace(round_trip_ms=9,measured_offset_ms=0)


def lease_fixture(*, interval=.012, early_eof=False, invalid_payload=False,
                  snapshot_fail=False, frame_burst=False):
    @asynccontextmanager
    async def lease(*,allow_public_market_data):
        assert allow_public_market_data
        state={"id":100}
        class Socket:
            @property
            def stream(self):
                async def generator():
                    i=0
                    while True:
                        await asyncio.sleep(0 if frame_burst else interval)
                        if early_eof and i>=14:
                            return
                        i+=1
                        state["id"]+=1
                        now=time.time_ns()//1_000_000
                        diff={"e":"depthUpdate","E":now,"s":"BTCUSDT",
                              "U":state["id"],"u":state["id"],
                              "b":[["100","2"]],"a":[]}
                        body={"stream":"btcusdt@depth@100ms","data":diff}
                        if invalid_payload and i==25:
                            body={"stream":"btcusdt@depth20@100ms","data":diff}
                        yield json.dumps(body,separators=(",",":")).encode()
                return generator()
            async def snapshot(self,symbol,limit):
                assert symbol=="BTCUSDT" and limit==1000
                if snapshot_fail:
                    raise OSError("test injected REST outage")
                await asyncio.sleep(0)
                return json.dumps({
                    "lastUpdateId":state["id"],
                    "bids":[["100","2"],["99","3"]],
                    "asks":[["102","2"],["103","4"]],
                }).encode()
        yield Socket()
    return lease


@pytest.mark.asyncio
async def test_one_socket_reader_all_frames_sealed_across_more_than_two_sqlite_journals(tmp_path):
    root=tmp_path/"rotated"
    result=await run_rotation(
        root,allow_public_market_data=True,
        duration_s=.75,max_frames_per_segment=16,_fixture=True,
        lease_factory=lease_fixture(),clock_preflight=public_clock,
    )
    assert result["verdict"]=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY",json.dumps(result,sort_keys=True)
    assert result["source_socket_count"]==1
    assert result["source_reader_task_count"]==1
    assert len(result["segments"])>=2
    assert result["produced_ws_frames"]==result["committed_ws_frames"]
    assert not (root/".forward_producer.lock").exists()
    audit=inspect(root)
    assert audit["verdict"]=="PASSED_BOUNDED_LOCAL_SOURCE_ROTATION_AUDIT_ONLY",audit
    assert audit["source_frames"]==result["produced_ws_frames"]
    assert audit["snapshots"]==len(result["segments"])
    assert audit["HMAC_downstream_audited"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",["early","invalid","snapshot"])
async def test_early_eof_malformed_and_rest_fault_quarantine_no_false_rotation_success(
    tmp_path,failure
):
    root=tmp_path/"bad"
    report=await run_rotation(
        root,allow_public_market_data=True,
        duration_s=.7,max_frames_per_segment=16,_fixture=True,
        lease_factory=lease_fixture(early_eof=failure=="early",
                                   invalid_payload=failure=="invalid",
                                   snapshot_fail=failure=="snapshot"),
        clock_preflight=public_clock,
    )
    assert report["verdict"]=="FAILED_OR_QUARANTINED",report
    assert report["orders_submitted"]==0
    assert report["exchange_history_completeness_verified"] is False


@pytest.mark.asyncio
async def test_rotation_refuses_network_without_consent_and_bad_cadence(tmp_path):
    with pytest.raises(PermissionError):
        await run_rotation(tmp_path/"no",duration_s=.7,_fixture=True,
                           lease_factory=lease_fixture(),clock_preflight=public_clock)
    assert not (tmp_path/"no").exists()
    with pytest.raises(ValueError):
        await run_rotation(tmp_path/"unsafe",allow_public_market_data=True,
                           duration_s=0.01,_fixture=True,
                           lease_factory=lease_fixture(),clock_preflight=public_clock)
    assert not (tmp_path/"unsafe").exists()


@pytest.mark.asyncio
async def test_stamped_receipt_requires_experimental_adapter_optin(tmp_path):
    journal=OfflineSpotCaptureJournal(
        tmp_path/"source.sqlite",mode_provider=lambda:QuantMode.SIMULATION)
    adapter=OptInSpotIngressAdapter(journal,symbol="BTCUSDT")
    async def stream():
        now=time.time_ns()//1_000_000
        payload={"stream":"btcusdt@depth@100ms",
                 "data":{"e":"depthUpdate","E":now,"s":"BTCUSDT",
                         "U":100,"u":101,"b":[],"a":[]}}
        yield SourceStampedSpotFrame(json.dumps(payload).encode(),now)
    async def snap(*args):
        return b"{}"
    with pytest.raises(Exception,match="stamp"):
        await adapter.capture_open_stream(stream(),snap,session_id="stamp_refuse")
    journal.close()


@pytest.mark.asyncio
async def test_rotation_tampered_original_cannot_pass_independent_audit(tmp_path):
    root=tmp_path/"tamper"
    report=await run_rotation(
        root,allow_public_market_data=True,
        duration_s=.65,max_frames_per_segment=16,_fixture=True,
        lease_factory=lease_fixture(),clock_preflight=public_clock,
    )
    assert report["verdict"]=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY",json.dumps(report,sort_keys=True)
    path=root/"segments"/"0001"/"original.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE offline_spot_capture_records SET raw_blob=? WHERE capture_id=(SELECT MIN(capture_id) FROM offline_spot_capture_records)",(b"{}",))
    with pytest.raises(ValueError,match="SHA"):
        inspect(root)


@pytest.mark.asyncio
async def test_capture_queue_overflow_quarantines_when_source_bursts(tmp_path):
    root=tmp_path/"burst"
    report=await run_rotation(
        root,allow_public_market_data=True,
        duration_s=.3,max_frames_per_segment=16,_fixture=True,
        lease_factory=lease_fixture(frame_burst=True),
        clock_preflight=public_clock,
    )
    assert report["verdict"]=="FAILED_OR_QUARANTINED",report
    assert report["orders_submitted"]==0


@pytest.mark.skipif(sys.platform!="linux",reason="subprocess os._exit gate requires Linux")
def test_real_process_death_at_rotation_preserves_sealed_original_and_orphan_owner(tmp_path):
    root=(tmp_path/"hard_crash_during_rotation").resolve()
    program=r'''
import asyncio,json,os,sys,time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from ci.phase1_rotating_spot_source import run_rotation
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal

source_start=OfflineSpotCaptureJournal.__init__
def kill_on_second_segment(self,path,*args,**kwargs):
    if Path(path).parent.name=="0001":
        # First journal has completed source/REST sync and fsynced seal.
        # Simulate abrupt whole-process termination BEFORE creating #2.
        os._exit(49)
    source_start(self,path,*args,**kwargs)
OfflineSpotCaptureJournal.__init__=kill_on_second_segment

async def clock(**kwargs):
    return SimpleNamespace(round_trip_ms=1,measured_offset_ms=0)
@asynccontextmanager
async def lease(*,allow_public_market_data):
    state={"id":100}
    class PublicFixture:
        @property
        def stream(self):
            async def frames():
                while True:
                    await asyncio.sleep(.008)
                    state["id"]+=1
                    ts=time.time_ns()//1_000_000
                    data={"e":"depthUpdate","E":ts,"s":"BTCUSDT",
                          "U":state["id"],"u":state["id"],
                          "b":[["100","2"]],"a":[]}
                    yield json.dumps({"stream":"btcusdt@depth@100ms",
                                      "data":data}).encode()
            return frames()
        async def snapshot(self,symbol,limit):
            return json.dumps({"lastUpdateId":state["id"],
                "bids":[["100","2"]],"asks":[["102","2"]]}).encode()
    yield PublicFixture()
asyncio.run(run_rotation(
    Path(sys.argv[1]),allow_public_market_data=True,duration_s=.6,
    max_frames_per_segment=8,_fixture=True,
    lease_factory=lease,clock_preflight=clock,
))
'''
    result=subprocess.run([sys.executable,"-c",program,str(root)],
        cwd=str(Path(__file__).resolve().parents[1]),
        capture_output=True,text=True,timeout=30)
    assert result.returncode==49,result.stderr[-1500:]
    assert (root/".forward_producer.lock").is_file()
    assert (root/"segments"/"0000"/"seal.json").is_file()
    assert not (root/"segments"/"0001"/"seal.json").exists()
    assert not (root/"aggregate_report.json").exists()
    with sqlite3.connect((root/"segments"/"0000"/"original.sqlite").resolve().as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM offline_forward_capture_intents").fetchone()[0]>=9
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_sessions WHERE closed_at_ms IS NOT NULL").fetchone()[0]==1
    with pytest.raises(ValueError,match="owner marker"):
        inspect(root)
