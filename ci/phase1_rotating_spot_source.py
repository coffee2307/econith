"""Experimental one-reader/one-socket, rotating SQLite Spot original journals.

No default runtime routes, execution, keys, HMAC downstream publication or
exchange-side gap-free attestation. A single pinned TLS socket is read by one
task for the whole monotonic duration. A separate bounded queue admits frames
to consecutive sealed SQLite sessions. Every rotation must replay to a synced
book with one fresh REST snapshot. Any loss/overflow/early EOF/quota/failed
seal quarantines the entire experiment; no auto reconnect or gap erasure.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sys
import time

from core.mode import QuantMode
from ci.phase1_public_spot_acceptance import measure_public_clock
from ci.phase1_single_socket_duration_trial import DurationStream, _fresh_private_root
from infrastructure.spot_forward_owner import exclusive_forward_owner
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SourceStampedSpotFrame, SpotTransportError,
    _PUBLIC_REST, _PUBLIC_WS,
)
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease

_SCHEMA="econith-phase1-single-reader-rotating-originals-v1"
_INITIAL_CHAIN=hashlib.sha256(b"ECONITH-SPOT-RESEARCH-ROTATION-V1").hexdigest()

def roll_hash(prior: str, ordinal: int, raw: bytes) -> str:
    """Local tamper-evident receipt sequence only, not a venue signature."""
    return hashlib.sha256(
        bytes.fromhex(prior)+ordinal.to_bytes(8,"big")+
        hashlib.sha256(raw).digest()
    ).hexdigest()


async def _capture_owned(root: Path, *,duration_s:float,max_frames_per_segment:int,
                         allow_network:bool,lease_factory,clock_preflight,
                         _fixture:bool=False,terminal_silence_s:float=5.0,
                         _on_segment_sealed=None,
                         _on_capture_complete=None):
    report={
        "schema":_SCHEMA,"verdict":"STARTED_NOT_CERTIFIED",
        "mode":"SIMULATION","source":"binance_public_data_only",
        "source_socket_count":0,"source_reader_task_count":0,
        "target_duration_s":duration_s,"segment_frame_limit":max_frames_per_segment,
        "max_segments":32,"source_queue_capacity":64,"adapter_queue_capacity":128,
        "max_terminal_source_silence_s":terminal_silence_s,
        "event_freshness_cap_ms":5000,"max_disk_bytes":150_000_000,
        "segments":[],"first_global_frame":1,
        "produced_ws_frames":0,"committed_ws_frames":0,
        "source_queue_high_watermark":0,
        "original_commit_age_ms_max":0,
        "original_commit_age_ms_histogram":{"0_100":0,"101_500":0,"501_1000":0,"1001_5000":0},
        "journal_disk_bytes_peak":0,
        "chain_start":_INITIAL_CHAIN,"chain_end":_INITIAL_CHAIN,
        "owner_fence_held":True,"orders_submitted":0,
        "HMAC_downstream_tested":False,"default_market_ingress_cutover":False,
        "exchange_history_completeness_verified":False,
        "venue_losslessness_verified":False,"institutional_phase1_complete":False,
    }
    reader=None
    started=None
    source_clock=lambda:time.time_ns()//1_000_000
    current_chain=_INITIAL_CHAIN
    event_ordinal=0
    frame_ordinal=0
    report_path=root/"aggregate_report.json"
    segments_root=root/"segments"
    try:
        clock=await clock_preflight(max_abs_offset_ms=3000,max_rtt_ms=2000)
        report["clock_rtt_ms"]=clock.round_trip_ms
        report["clock_offset_ms"]=clock.measured_offset_ms
        segments_root.mkdir(mode=0o700)
        async with lease_factory(allow_public_market_data=True) as lease:
            if lease_factory is open_pinned_public_spot_lease:
                lease.ensure_open()
            report["source_socket_count"]=1
            queue:asyncio.Queue[SourceStampedSpotFrame]=asyncio.Queue(maxsize=64)
            stream=DurationStream(lease.stream,duration_s=duration_s,
                max_terminal_silence_s=terminal_silence_s)
            started=time.monotonic()

            async def single_source_reader():
                async for raw in stream:
                    if type(raw) not in (str,bytes):
                        raise SpotTransportError(
                            "unknown public WebSocket raw transport type",
                            reason="SOURCE_BODY_TYPE",
                        )
                    if report["produced_ws_frames"]>=100_000:
                        raise SpotTransportError(
                            "research source frame count quota exhausted",
                            reason="SOURCE_TOTAL_FRAME_CAP",
                        )
                    stamp=SourceStampedSpotFrame(raw,source_clock())
                    try:
                        queue.put_nowait(stamp)
                    except asyncio.QueueFull as exc:
                        raise SpotTransportError(
                            "uninterrupted reader upstream FIFO overflow",
                            reason="ROTATING_SOURCE_FIFO_OVERFLOW",
                        ) from exc
                    report["produced_ws_frames"]+=1
                    report["source_queue_high_watermark"]=max(
                        report["source_queue_high_watermark"],queue.qsize()
                    )
                    await asyncio.sleep(0)

            reader=asyncio.create_task(single_source_reader(),
                                       name="phase1-one-socket-lifetime-reader")
            report["source_reader_task_count"]=1

            async def next_stamped():
                # Transport reader and writer remain separate across rollover.
                # Prefer actual queued frames even if producer just exited.
                while True:
                    if not queue.empty():
                        return queue.get_nowait()
                    if reader.done():
                        await reader   # propagates source EOF/overflow/idle.
                        return None
                    waiter=asyncio.create_task(queue.get())
                    try:
                        done,_=await asyncio.wait(
                            {waiter,reader},return_when=asyncio.FIRST_COMPLETED
                        )
                        if waiter in done:
                            return waiter.result()
                    finally:
                        if not waiter.done():
                            waiter.cancel()
                            await asyncio.gather(waiter,return_exceptions=True)

            async def each_segment(first:SourceStampedSpotFrame):
                yield first
                while True:
                    nxt=await next_stamped()
                    if nxt is None:
                        return
                    yield nxt

            while True:
                first=await next_stamped()
                if first is None:
                    break
                if len(report["segments"])>=32:
                    raise SpotTransportError(
                        "rotating journal segment budget exhausted",
                        reason="ROTATION_SEGMENT_CAP",
                    )
                segment_number=len(report["segments"])
                sd=segments_root/f"{segment_number:04d}"
                sd.mkdir(mode=0o700)
                journal=OfflineSpotCaptureJournal(
                    sd/"original.sqlite",
                    mode_provider=lambda:QuantMode.SIMULATION,
                    max_body_bytes=262144,max_frames_per_session=max_frames_per_segment+1,
                    max_age_ms=5000,forward_capture_audit=True,reuse_connection=True,
                )
                sid="rec_rot_"+secrets.token_hex(8)
                adapter=OptInSpotIngressAdapter(
                    journal,symbol="BTCUSDT",queue_capacity=128,
                    cooperative_reader_yield=True,accept_source_stamped_frames=True,
                )
                prior_chain=current_chain
                first_frame=frame_ordinal+1
                try:
                    async def source_commit_guard(rec):
                        diff=source_clock()-rec.received_at_ms
                        if 0<=diff<=5000:
                            report["original_commit_age_ms_max"]=max(
                                report["original_commit_age_ms_max"],diff
                            )
                            group=("0_100" if diff<=100 else
                                   "101_500" if diff<=500 else
                                   "501_1000" if diff<=1000 else "1001_5000")
                            report["original_commit_age_ms_histogram"][group]+=1
                        if diff<0 or diff>5000:
                            raise SpotTransportError(
                                "stored source original aged beyond 5s",
                                reason="ROTATION_COMMIT_AGE",
                            )
                    result=await adapter.capture_open_stream(
                        each_segment(first),lease.snapshot,session_id=sid,
                        source="binance_public_simulation",
                        ws_endpoint=_PUBLIC_WS,rest_endpoint=_PUBLIC_REST,
                        max_frames=max_frames_per_segment,
                        timeout_s=min(600.,duration_s+60.),
                        on_persisted=source_commit_guard,
                        idle_timeout_s=30.,require_frame_limit=False,
                        source_opened_at_ms=first.received_at_ms,
                    )
                    records=journal.verify_forward_capture_inventory(
                        sid,limit=max_frames_per_segment+1
                    )
                    if (result.depth_status!="SYNCED"
                        or result.snapshot_captures!=1
                        or len(records)!=result.websocket_frames+1
                        or result.queue_high_watermark>=128):
                        raise SpotTransportError(
                            "rotated source original seal refused",
                            reason="ROTATED_SEAL_UNSYNCED",
                        )
                    for rec in records:
                        event_ordinal+=1
                        current_chain=roll_hash(current_chain,event_ordinal,rec.raw)
                    frame_ordinal+=result.websocket_frames
                    seal={
                        "index":segment_number,"session_id":sid,
                        "first_frame_ordinal":first_frame,
                        "last_frame_ordinal":frame_ordinal,
                        "original_events":len(records),
                        "websocket_frames":result.websocket_frames,
                        "snapshot_captures":result.snapshot_captures,
                        "queue_peak":result.queue_high_watermark,
                        "depth_status":result.depth_status,
                        "chain_start":prior_chain,"chain_end":current_chain,
                    }
                    with (sd/"seal.json").open("x",encoding="utf-8") as f:
                        json.dump(seal,f,sort_keys=True,indent=2)
                        f.write("\n")
                        f.flush()
                        os.fsync(f.fileno())
                    report["segments"].append(seal)
                    report["committed_ws_frames"]=frame_ordinal
                finally:
                    journal.close()
                # Keep source original and WAL/index bytes inside strict bounds.
                used=sum(f.stat().st_size for f in segments_root.rglob("*")
                         if f.is_file())
                report["journal_disk_bytes_peak"]=max(
                    report["journal_disk_bytes_peak"],used
                )
                if used>report["max_disk_bytes"]:
                    raise SpotTransportError(
                        "rotating journal exceeded private disk budget",
                        reason="ROTATING_STORAGE_CAP",
                    )
                if _on_segment_sealed is not None:
                    # Experimental callback fires ONLY after original journal
                    # closure and fsynced per-segment seal. It must enqueue
                    # without blocking the lifetime WebSocket reader.
                    _on_segment_sealed(sd,seal)
                # Yield CPU scheduling to the one uninterrupted upstream reader.
                await asyncio.sleep(0)
            await reader
            report["duration_stream_elapsed_s"]=stream.elapsed_s
            report["terminal_source_silence_s"]=stream.terminal_silence_s
            report["duration_target_reached"]=stream.target_reached
            report["chain_end"]=current_chain
            if (not stream.target_reached or stream.early_eof
                or stream.elapsed_s<duration_s-0.05
                or not 2<=len(report["segments"])<=32
                or not 1<=report["source_queue_high_watermark"]<64
                or report["produced_ws_frames"]!=frame_ordinal
                or report["terminal_source_silence_s"] is None
                or report["terminal_source_silence_s"]>terminal_silence_s):
                raise SpotTransportError(
                    "one-socket rotating source inventory incomplete",
                    reason="ROTATION_INVENTORY_INCOMPLETE",
                )
            report["verdict"]="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY"
    except Exception as exc:
        report["verdict"]="FAILED_OR_QUARANTINED"
        report["failure_class"]=type(exc).__name__
        if isinstance(exc,SpotTransportError):
            report["failure_code"]=exc.reason
    finally:
        if reader is not None and not reader.done():
            reader.cancel()
            await asyncio.gather(reader,return_exceptions=True)
        report["chain_end"]=current_chain
        # Snapshot process metrics before the downstream completion hook
        # builds and fsyncs its independently audited aggregate.
        try:
            import resource
            usage=resource.getrusage(resource.RUSAGE_SELF)
            report["process_cpu_user_s"]=round(usage.ru_utime,3)
            report["process_cpu_system_s"]=round(usage.ru_stime,3)
            report["process_peak_rss_kib_linux"]=int(usage.ru_maxrss) if sys.platform.startswith("linux") else None
        except ImportError:
            report["process_peak_rss_kib_linux"]=None
        if _on_capture_complete is not None:
            try:
                await _on_capture_complete(report)
            except Exception as exc:
                report["verdict"]="FAILED_OR_QUARANTINED"
                report["downstream_failure_class"]=type(exc).__name__
                report["downstream_incomplete"]=True
        report["aggregate_elapsed_s"]=round(time.monotonic()-started,3) if started is not None else 0
        try:
            import resource
            usage=resource.getrusage(resource.RUSAGE_SELF)
            report["process_cpu_user_s"]=round(usage.ru_utime,3)
            report["process_cpu_system_s"]=round(usage.ru_stime,3)
            report["process_peak_rss_kib_linux"]=int(usage.ru_maxrss) if sys.platform.startswith("linux") else None
        except ImportError:
            report["process_peak_rss_kib_linux"]=None
        with report_path.open("x",encoding="utf-8") as f:
            json.dump(report,f,sort_keys=True,indent=2,allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
    return report


async def run_rotation(root:Path,*,allow_public_market_data:bool=False,
                       duration_s:float=120.,max_frames_per_segment:int=300,
                       lease_factory=open_pinned_public_spot_lease,
                       clock_preflight=measure_public_clock,_fixture:bool=False,
                       terminal_silence_s:float=5.0,
                       _on_segment_sealed=None,
                       _on_capture_complete=None,
                       _public_corun_authorized:bool=False)->dict:
    if allow_public_market_data is not True:
        raise PermissionError("read-only public source opt-in required")
    if (type(duration_s) not in (int,float) or not math.isfinite(duration_s)
        or not ((.2<=duration_s<=180) if _fixture else (60<=duration_s<=180))
        or type(max_frames_per_segment) is not int
        or not ((8<=max_frames_per_segment<=800) if _fixture
                else (160<=max_frames_per_segment<=800))
        or type(terminal_silence_s) not in (int,float)
        or not math.isfinite(terminal_silence_s)
        or not .01<=terminal_silence_s<=5.0
        or (_on_segment_sealed is not None
            and (not callable(_on_segment_sealed)
                 or not (_fixture or
                     (_public_corun_authorized is True
                      and lease_factory is open_pinned_public_spot_lease))))
        or (_on_capture_complete is not None
            and (not callable(_on_capture_complete)
                 or not (_fixture or
                     (_public_corun_authorized is True
                      and lease_factory is open_pinned_public_spot_lease))))
        or type(_public_corun_authorized) is not bool
        or (_public_corun_authorized
            and (_fixture or lease_factory is not open_pinned_public_spot_lease))):
        raise ValueError("strict experimental source rotation constraints")
    _fresh_private_root(root)
    with exclusive_forward_owner(root,role="CAPTURE"):
        return await _capture_owned(
            root,duration_s=duration_s,
            max_frames_per_segment=max_frames_per_segment,
            allow_network=True,lease_factory=lease_factory,
            clock_preflight=clock_preflight,_fixture=_fixture,
            terminal_silence_s=terminal_silence_s,
            _on_segment_sealed=_on_segment_sealed,
            _on_capture_complete=_on_capture_complete,
        )


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--acknowledge-rotation-not-gap-free",action="store_true")
    p.add_argument("--duration-s",type=float,default=120.0)
    p.add_argument("--frames-per-segment",type=int,default=300)
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args(argv)
    if not (args.allow_public_market_data
            and args.acknowledge_rotation_not_gap_free):
        p.error("explicit source-only bounded SIMULATION consent required")
    try:
        report=asyncio.run(run_rotation(
            args.output_dir,allow_public_market_data=True,
            duration_s=args.duration_s,max_frames_per_segment=args.frames_per_segment,
        ))
    except (OSError,ValueError,PermissionError) as exc:
        print(json.dumps({"verdict":"REFUSED","failure_class":type(exc).__name__,
                          "orders_submitted":0}))
        return 2
    print(json.dumps({k:v for k,v in report.items() if k!="segments"},
                     sort_keys=True,allow_nan=False))
    return 0 if report["verdict"]=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY" else 2


if __name__=="__main__":
    sys.exit(main())
