"""Offline parallel receiver projection while ONE Spot reader rotates SQLite source.

No public network or live orders. Long-running child IPC uses separate thread,
so source reader stays responsive; bounded sealed-segment queue or any delayed/
failed HMAC handoff marks full test quarantined.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from ci.phase1_rotating_corun_hmac import co_run_rotating_hmac
from ci.phase1_rotating_hmac_audit import inspect as inspect_hmac


async def clock(**kwargs):
    assert kwargs=={"max_abs_offset_ms":3000,"max_rtt_ms":2000}
    return SimpleNamespace(round_trip_ms=7,measured_offset_ms=0)


@asynccontextmanager
async def stream_fixture(*,allow_public_market_data,segment_size:int=12):
    assert allow_public_market_data is True
    state={"id":100,"snapshot_index":0}
    class Lease:
        @property
        def stream(self):
            async def source():
                while True:
                    await asyncio.sleep(.055)
                    state["id"]+=1
                    ts=time.time_ns()//1_000_000
                    body={"stream":"btcusdt@depth@100ms","data":{
                        "e":"depthUpdate","E":ts,"s":"BTCUSDT",
                        "U":state["id"],"u":state["id"],
                        "b":[["100","2"]],"a":[]}}
                    yield json.dumps(body,separators=(",",":")).encode()
            return source()
        async def snapshot(self,symbol,limit):
            assert symbol=="BTCUSDT" and limit==1000
            state["snapshot_index"]+=1
            # Fixture-only Binance initial REST bridge: snapshot must cover
            # the first buffered diff U of this exact source journal, even
            # under arbitrary scheduler delay.
            expected=101+segment_size*(state["snapshot_index"]-1)
            return json.dumps({"lastUpdateId":expected,
                "bids":[["100","2"],["99","2"]],
                "asks":[["102","2"],["103","2"]]}).encode()
    yield Lease()


@asynccontextmanager
async def eight_frame_stream_fixture(*,allow_public_market_data):
    async with stream_fixture(
        allow_public_market_data=allow_public_market_data,segment_size=8
    ) as lease:
        yield lease


@pytest.mark.asyncio
async def test_source_reader_and_real_child_receiver_overlap_with_ordinals_and_checkpoints(tmp_path):
    root=(tmp_path/"overlap").resolve()
    result=await co_run_rotating_hmac(
        root,duration_s=2.65,max_frames_per_segment=12,
        lease_factory=stream_fixture,clock_preflight=clock,
        explicit_offline_fixture_approval=True,
    )
    assert result["verdict"]=="PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY",json.dumps(result,sort_keys=True)
    assert result["concurrent_segments_completed"]>=1
    assert len(result["segments"])>=2
    assert result["segment_backlog_peak"]<=2
    assert 1<=result["outstanding_hmac_segments_peak"]<=3
    if sys.platform=="linux":
        assert result["child_peak_rss_kib_linux"]>0
        assert result["child_cpu_user_s"]>=0
        assert result["child_cpu_system_s"]>=0
        assert result["child_voluntary_context_switches"]>=0
    assert result["independent_local_audit_passed"] is True
    assert result["reconstructed_projections"]==result["forward_checkpoints"]
    assert result["reconstructed_projections"]==result["independent_receiver_receipts"]
    assert result["named_subscriber_receipts"]==2*result["derived_receipts"]
    assert not (root/".forward_producer.lock").exists()
    inspection=inspect_hmac(root,co_run=True)
    assert inspection["verdict"]=="PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY"
    assert inspection["source_projections"]==result["reconstructed_projections"]
    assert inspection["continuous_live_hmac_verified"] is False
    # Independent observer receipts must be read/rehashed from all three
    # actual durable SQLite ledgers, never counted from the producer summary.
    primary=root/"segments"/"0000"/"derived.sqlite"
    with sqlite3.connect(primary) as db:
        old=db.execute(
            "SELECT receipt_id FROM offline_research_derived_receipts LIMIT 1"
        ).fetchone()[0]
        db.execute(
            "UPDATE offline_research_derived_receipts SET receipt_id=? "
            "WHERE rowid=(SELECT MIN(rowid) FROM offline_research_derived_receipts)",
            ("f"*64,)
        )
    with pytest.raises(ValueError,match="derived receipt"):
        inspect_hmac(root,co_run=True)
    with sqlite3.connect(primary) as db:
        db.execute(
            "UPDATE offline_research_derived_receipts SET receipt_id=? "
            "WHERE rowid=(SELECT MIN(rowid) FROM offline_research_derived_receipts)",
            (old,)
        )
    named=root/"segments"/"0000"/"research_monitor.sqlite"
    with sqlite3.connect(named) as db:
        db.execute(
            "UPDATE offline_research_derived_receipts SET projection_digest=? "
            "WHERE rowid=(SELECT MIN(rowid) FROM offline_research_derived_receipts)",
            ("e"*64,)
        )
    with pytest.raises(ValueError,match="derived receipt"):
        inspect_hmac(root,co_run=True)


@pytest.mark.asyncio
async def test_injected_receiver_worker_failure_quarantines_without_global_pass(tmp_path):
    root=(tmp_path/"worker_fail").resolve()
    response=await co_run_rotating_hmac(
        root,duration_s=1.2,max_frames_per_segment=8,
        lease_factory=eight_frame_stream_fixture,clock_preflight=clock,
        explicit_offline_fixture_approval=True,inject_segment_failure=0,
    )
    assert response["verdict"]=="FAILED_OR_QUARANTINED",response
    assert response["orders_submitted"]==0
    assert (root/"hmac_corun_quarantine.json").is_file()
    assert not (root/"hmac_corun_aggregate.json").exists()
    with pytest.raises((ValueError,FileNotFoundError)):
        inspect_hmac(root,co_run=True)


@pytest.mark.asyncio
async def test_co_running_disallowed_without_explicit_fixture_consent(tmp_path):
    root=tmp_path/"without_consent"
    with pytest.raises(PermissionError):
        await co_run_rotating_hmac(root,duration_s=1.5,max_frames_per_segment=8,
            lease_factory=stream_fixture,clock_preflight=clock)
    assert not root.exists()


@pytest.mark.skipif(sys.platform!="linux",reason="real process crash test requires Linux")
def test_hard_kill_after_independent_receipt_before_producer_ack_during_live_capture(tmp_path):
    root=(tmp_path/"kill_corun").resolve()
    program=r'''
import asyncio,os,sys,json,time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from ci.phase1_rotating_corun_hmac import co_run_rotating_hmac
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox

original=OfflineForwardSpotOutbox.ack_receiver
def crash_before_producer_ack(self, envelope, receipt):
    # Separate receiver child has committed the durable receipt.
    os._exit(63)
OfflineForwardSpotOutbox.ack_receiver=crash_before_producer_ack

async def clock(**kwargs):
    return SimpleNamespace(round_trip_ms=7,measured_offset_ms=0)
@asynccontextmanager
async def lease(*,allow_public_market_data):
    state={"id":100,"snapshot_index":0}
    class Socket:
        @property
        def stream(self):
            async def ws():
                while True:
                    await asyncio.sleep(.012)
                    state["id"]+=1
                    ts=time.time_ns()//1_000_000
                    data={"e":"depthUpdate","E":ts,"s":"BTCUSDT",
                          "U":state["id"],"u":state["id"],
                          "b":[["100","2"]],"a":[]}
                    yield json.dumps({"stream":"btcusdt@depth@100ms","data":data}).encode()
            return ws()
        async def snapshot(self,symbol,limit):
            state["snapshot_index"]+=1
            return json.dumps({"lastUpdateId":101+12*(state["snapshot_index"]-1),
                  "bids":[["100","2"]],"asks":[["102","2"]]}).encode()
    yield Socket()
asyncio.run(co_run_rotating_hmac(
    Path(sys.argv[1]),duration_s=3.8,max_frames_per_segment=12,
    explicit_offline_fixture_approval=True,
    receiver_batch_size=8,reuse_receiver_connection=True,
    lease_factory=lease,clock_preflight=clock))
'''
    r=subprocess.run([sys.executable,"-c",program,str(root)],
        capture_output=True,text=True,timeout=70,
        cwd=str(Path(__file__).resolve().parents[1]))
    assert r.returncode==63,r.stderr[-1200:]
    assert (root/".forward_producer.lock").is_file()
    assert (root/"segments"/"0000"/"seal.json").is_file()
    assert (root/"segments"/"0000"/".forward_producer.lock").is_file()
    assert not (root/"hmac_corun_aggregate.json").exists()
    recv=root/"segments"/"0000"/"independent_receiver.sqlite"
    with sqlite3.connect(recv.resolve().as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM offline_independent_projection_receipts").fetchone()[0]>=1
    with pytest.raises(ValueError,match="operator review"):
        inspect_hmac(root,co_run=True)

    # Verified OS subprocess death. This is a synthetic *operator-reviewed*
    # local recovery only: production code never unlinks orphan markers.
    child=root/"segments"/"0000"
    with sqlite3.connect(recv.resolve().as_uri()+"?mode=ro",uri=True) as db:
        prior={
            (row[0],row[1]) for row in db.execute(
                "SELECT ingest_id,receipt_id FROM offline_independent_projection_receipts"
            )
        }
    assert len(prior)>=2, "must exercise multiple already-COMMITted batched ACKs"
    (root/".forward_producer.lock").unlink()
    (child/".forward_producer.lock").unlink()
    from infrastructure.spot_forward_recovery import recover_staged_forward_spot
    session=json.loads((child/"seal.json").read_text())["session_id"]
    recovered=asyncio.run(recover_staged_forward_spot(
        root=child,session_id=session,max_envelopes=1000,
        reconstruct_missing=True,max_book_staleness_ms=5000,
        persistent_receiver=True,receiver_batch_size=8,
        reuse_receiver_connection=True,
    ))
    assert recovered.staged_envelopes==recovered.receiver_receipts==recovered.forward_checkpoints
    assert recovered.source_coverage_certified is False
    assert recovered.session_certified is False
    with sqlite3.connect(recv.resolve().as_uri()+"?mode=ro",uri=True) as db:
        after={
            (row[0],row[1]) for row in db.execute(
                "SELECT ingest_id,receipt_id FROM offline_independent_projection_receipts"
            )
        }
    assert prior.issubset(after)
    assert len(after)==recovered.receiver_receipts
    assert len(after)==len({identity for identity,digest in after})
    # Even though one sealed segment recovers correctly, the overall source
    # was interrupted and must NOT become a globally certified source.
    with pytest.raises((ValueError,FileNotFoundError)):
        inspect_hmac(root,co_run=True)


@pytest.mark.asyncio
async def test_authenticated_eight_receipt_batch_with_receiver_connection_reuse(tmp_path):
    """Eight individually persisted ACKs per authenticated HMAC request."""
    root=(tmp_path/"hmac_batch_eight").resolve()
    result=await co_run_rotating_hmac(
        root,duration_s=2.65,max_frames_per_segment=12,
        lease_factory=stream_fixture,clock_preflight=clock,
        explicit_offline_fixture_approval=True,
        receiver_batch_size=8,reuse_receiver_connection=True,
    )
    assert result["verdict"]=="PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY",result
    assert result["receiver_batch_size"]==8
    assert result["receiver_connection_reuse"] is True
    assert result["reconstructed_projections"]==result["independent_receiver_receipts"]==result["forward_checkpoints"]
    assert result["named_subscriber_receipts"]==2*result["derived_receipts"]
    assert inspect_hmac(root,co_run=True)["verdict"]=="PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY"


@pytest.mark.asyncio
async def test_invalid_or_unauthorized_research_batch_size_rejected(tmp_path):
    root=(tmp_path/"invalid_batch").resolve()
    with pytest.raises(ValueError):
        await co_run_rotating_hmac(
            root,duration_s=2.65,max_frames_per_segment=12,
            lease_factory=stream_fixture,clock_preflight=clock,
            explicit_offline_fixture_approval=True,receiver_batch_size=33,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_deliberately_slow_receiver_overflows_bounded_segment_queue_fail_closed(tmp_path):
    """Stress producer faster than intentionally paused receiver: NO silent loss."""
    root=(tmp_path/"saturated_receiver").resolve()
    result=await co_run_rotating_hmac(
        root,duration_s=2.65,max_frames_per_segment=8,
        lease_factory=eight_frame_stream_fixture,clock_preflight=clock,
        explicit_offline_fixture_approval=True,
        receiver_batch_size=8,reuse_receiver_connection=True,
        _offline_worker_delay_s=4.0,
    )
    assert result["verdict"]=="FAILED_OR_QUARANTINED",result
    assert result["source_failure_code"]=="HMAC_SEGMENT_BACKPRESSURE",result
    assert result["orders_submitted"]==0
    assert not (root/"hmac_corun_aggregate.json").exists()
    assert (root/"hmac_corun_quarantine.json").is_file()
    with pytest.raises((ValueError,FileNotFoundError)):
        inspect_hmac(root,co_run=True)


@pytest.mark.asyncio
async def test_delay_injection_invalid_without_nontrading_fixture_scope(tmp_path):
    from ci.phase1_public_spot_acceptance import measure_public_clock
    from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease
    root=(tmp_path/"rejected_public_delay").resolve()
    with pytest.raises(ValueError):
        await co_run_rotating_hmac(
            root,duration_s=120,max_frames_per_segment=450,
            lease_factory=open_pinned_public_spot_lease,
            clock_preflight=measure_public_clock,
            explicit_public_research_approval=True,
            _offline_worker_delay_s=1.,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_reject_nonfinite_or_negative_offline_receiver_delays(tmp_path):
    for delay in (-1.,float("nan"),float("inf"),6.1):
        root=(tmp_path/"invalid_delay").resolve()
        with pytest.raises(ValueError):
            await co_run_rotating_hmac(
                root,duration_s=2.65,max_frames_per_segment=8,
                lease_factory=stream_fixture,clock_preflight=clock,
                explicit_offline_fixture_approval=True,
                _offline_worker_delay_s=delay,
            )
        assert not root.exists()
