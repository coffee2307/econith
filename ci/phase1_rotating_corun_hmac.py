"""Strict OFFLINE one-reader/rotating-segment co-running authenticated HMAC gate.

A single receiver worker schedules sealed-segment handoff in a separate thread
while the one simulated source WebSocket reader remains attached. Every
downstream handoff is still post-SEAL per segment (not per-event zero-latency).
No Binance/public source requests and no default runtime or trading access.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import time

from ci.phase1_rotating_hmac_handoff import handoff_one_sealed_segment, _fsync_json
from ci.phase1_rotating_spot_source import run_rotation
from ci.phase1_rotating_hmac_audit import inspect as independent_receipt_audit
from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_public_capture_adapter import SpotTransportError
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease

_SCHEMA="econith-phase1-rotating-hmac-corun-v1"
_PASS="PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY"


async def co_run_rotating_hmac(
    root:Path, *, duration_s:float, max_frames_per_segment:int,
    lease_factory,clock_preflight,
    explicit_offline_fixture_approval:bool=False,
    explicit_public_research_approval:bool=False,
    max_handoff_age_s:float=20.0,
    inject_segment_failure:int|None=None,
) -> dict:
    if ((explicit_offline_fixture_approval is True)
        == (explicit_public_research_approval is True)):
        raise PermissionError("exactly one research-only offline/public consent required")
    public=explicit_public_research_approval is True
    if (not callable(lease_factory) or not callable(clock_preflight)
        or (public and (
            lease_factory is not open_pinned_public_spot_lease
            or clock_preflight is not measure_public_clock))
        or (not public and lease_factory is open_pinned_public_spot_lease)
        or (public and not (
            type(duration_s) in (int,float) and math.isfinite(duration_s)
            and 90<=duration_s<=150
            and type(max_frames_per_segment) is int
            and 250<=max_frames_per_segment<=650))

        or not isinstance(root,Path) or not root.is_absolute()
        or type(max_handoff_age_s) not in (int,float)
        or not math.isfinite(max_handoff_age_s)
        or not 1 <= max_handoff_age_s<=30
        or inject_segment_failure is not None
        and (type(inject_segment_failure) is not int or inject_segment_failure<0)):
        raise ValueError("offline bounded HMAC co-run fixture constraint")
    pending:asyncio.Queue=asyncio.Queue(maxsize=2)
    completed=[]
    lag_ms=[]
    failures=[]
    overlap=0
    source_finished=asyncio.Event()
    start_monotonic=time.monotonic()
    result={
        "schema":_SCHEMA,"verdict":"INCOMPLETE_NOT_CERTIFIED",
        "mode":"SIMULATION","source_reader_attached_during_hmac":True,
        "public_read_only_market_source":public,
        "source_socket_count_observed":1,
        "postseal_handoff_only":False,"per_segment_postseal_delivery":True,
        "unobserved_venue_history_certified":False,
        "full_continuous_market_hmac_certified":False,
        "live_orders_authorized":False,"orders_submitted":0,
        "default_ingress_cutover":False,"institutional_phase1_complete":False,
        "segments":[],"originals":0,"reconstructed_projections":0,
        "independent_receiver_receipts":0,"derived_receipts":0,
        "named_subscriber_receipts":0,"forward_checkpoints":0,
        "concurrent_segments_completed":0,
        "segment_handoff_lag_max_ms":0,"segment_backlog_peak":0,
        "segment_queue_capacity":2,
        "segment_handoff_age_budget_ms":int(max_handoff_age_s*1000),
    }

    async def worker():
        nonlocal overlap
        while True:
            batch=await pending.get()
            try:
                if batch is None:
                    return
                sd,seal,admitted_at=batch
                if failures:
                    continue
                try:
                    if inject_segment_failure==seal["index"]:
                        raise RuntimeError("injected independent receiver outage")
                    # Existing local child-process HMAC IPC / derived proof
                    # performs blocking SQLite and full replay. Move it OFF
                    # source reader's asyncio loop, rather than blocking WSS.
                    evidence=await asyncio.to_thread(
                        lambda: asyncio.run(
                            handoff_one_sealed_segment(sd,seal,seal["index"])
                        )
                    )
                    interval_ms=round((time.monotonic()-admitted_at)*1000,3)
                    if interval_ms>max_handoff_age_s*1000:
                        raise RuntimeError("segment receiver/checkpoint ACK age budget exceeded")
                    lag_ms.append(interval_ms)
                    completed.append(evidence)
                    if not source_finished.is_set():
                        overlap+=1
                except Exception as exc:
                    failures.append(type(exc).__name__)
            finally:
                pending.task_done()

    task=asyncio.create_task(worker(),name="phase1-rotating-hmac-consumer")

    def on_segment(sd:Path,seal:dict)->None:
        if failures or task.done():
            raise SpotTransportError(
                "independent HMAC worker failed or stopped during source read",
                reason="HMAC_WORKER_FAILED",
            )
        try:
            pending.put_nowait((sd,seal,time.monotonic()))
        except asyncio.QueueFull as exc:
            raise SpotTransportError(
                "HMAC segment backlog exceeded 2 sealed journals",
                reason="HMAC_SEGMENT_BACKPRESSURE",
            ) from exc
        result["segment_backlog_peak"]=max(
            result["segment_backlog_peak"],pending.qsize()
        )

    async def on_source_complete(source_report:dict):
        source_finished.set()
        try:
            if task.done():
                await task
            else:
                await asyncio.wait_for(pending.put(None),timeout=10)
                await asyncio.wait_for(task,timeout=45)
            if failures or source_report.get("verdict")!="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY":
                raise RuntimeError("quarantined source or incomplete child receiver")
            ordered=sorted(completed,key=lambda x:x["index"])
            if (len(ordered)!=len(source_report.get("segments",[]))
                or [x["index"] for x in ordered]!=list(range(len(ordered)))
                or not overlap
                or not lag_ms):
                raise RuntimeError("missing concurrent source/receiver segment acknowledgements")
            result["segments"]=ordered
            result["sealed_segment_count"]=len(ordered)
            result["concurrent_segments_completed"]=overlap
            result["segment_handoff_lag_max_ms"]=max(lag_ms)
            for item in ordered:
                result["originals"]+=item["raw_original_events"]
                result["reconstructed_projections"]+=item["reconstructed_projections"]
                result["independent_receiver_receipts"]+=item["receiver_receipts"]
                result["derived_receipts"]+=item["primary_derived_receipts"]
                result["named_subscriber_receipts"]+=item["independent_named_receipts"]
                result["forward_checkpoints"]+=item["forward_checkpoints"]
            if (result["originals"]!=sum(s["original_events"]
                                        for s in source_report["segments"])
                or not result["reconstructed_projections"]
                or result["reconstructed_projections"]!=result["independent_receiver_receipts"]
                or result["reconstructed_projections"]!=result["forward_checkpoints"]
                or result["named_subscriber_receipts"]!=2*result["derived_receipts"]):
                raise RuntimeError("HMAC child receipt/derived/checkpoint parity mismatch")
            result["source_original_commit_age_ms_max"]=source_report.get("original_commit_age_ms_max")
            result["source_original_commit_age_histogram"]=source_report.get("original_commit_age_ms_histogram")
            result["source_queue_high_watermark"]=source_report.get("source_queue_high_watermark")
            result["adapter_queue_capacity"]=source_report.get("adapter_queue_capacity")
            result["source_ws_frames"]=source_report.get("committed_ws_frames")
            result["source_socket_observed_duration_s"]=source_report.get("duration_stream_elapsed_s")
            result["source_journal_disk_peak_bytes"]=source_report.get("journal_disk_bytes_peak")
            result["process_peak_rss_kib_linux"]=source_report.get("process_peak_rss_kib_linux")
            result["process_cpu_user_s"]=source_report.get("process_cpu_user_s")
            result["process_cpu_system_s"]=source_report.get("process_cpu_system_s")
            result["verdict"]=_PASS
            _fsync_json(root/"hmac_corun_aggregate.json",result)
        except BaseException as exc:
            # No operator-free retries or false completion. The outer
            # source owner marker is still held by this callback.
            _fsync_json(root/"hmac_corun_quarantine.json",{
                "verdict":"QUARANTINED_REQUIRES_OPERATOR_REVIEW",
                "failure_class":type(exc).__name__,
                "downstream_segments_completed":len(completed),
                "source_history_certified":False,
                "orders_submitted":0,
            })
            raise

    try:
        source=await run_rotation(
            root,allow_public_market_data=True,duration_s=duration_s,
            max_frames_per_segment=max_frames_per_segment,
            lease_factory=lease_factory,clock_preflight=clock_preflight,
            _fixture=not public,
            _public_corun_authorized=public,
            _on_segment_sealed=on_segment,
            _on_capture_complete=on_source_complete,
        )
    finally:
        # Source completion hook owns normal worker shutdown. For a preflight
        # or filesystem exception, avoid silently leaving a worker awaiting.
        if not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
    if source["verdict"]!="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY":
        return {
            "schema":_SCHEMA,"verdict":"FAILED_OR_QUARANTINED",
            "source_failure_class":source.get("failure_class"),
            "source_failure_code":source.get("failure_code"),
            "downstream_incomplete":True,"orders_submitted":0,
        }
    # Root owner has cleanly exited. This is not raw historical Binance proof.
    proof=independent_receipt_audit(root,co_run=True)
    if proof["verdict"]!="PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY":
        raise RuntimeError("independent read-only downstream acceptance refused")
    return dict(result,independent_local_audit_passed=True,
                independently_audited_reconstructed_projections=proof["source_projections"])
