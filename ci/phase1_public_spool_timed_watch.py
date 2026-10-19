"""Finite 10-minute research *observation watch* over separate public source sessions.

Every segment owns fresh WSS/REST and complete immutable/ACK/cursor ledgers.
Unknown gaps BETWEEN segments stay UNKNOWN. This is explicitly NOT an
uninterrupted 10-minute WSS, a 24h soak, or a live trading readiness gate.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import secrets
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ci.phase1_public_live_spool_trial import run as trial
from ci.phase1_cloud_resources import host_snapshot as portable_host_snapshot
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code


@dataclass(frozen=True)
class WatchPolicy:
    min_watch_s: float = 600.0
    max_watch_s: float = 900.0
    max_segments: int = 16
    frames_per_segment: int = 1000
    max_lag_ms: int = 5000
    source_timeout_s: float = 120.0
    max_storage_bytes: int = 500_000_000
    min_free_disk_bytes: int = 2_000_000_000
    min_free_memory_bytes: int = 1_500_000_000
    per_segment_hard_timeout_s: float = 240.0

    def validate(self) -> None:
        if (type(self.min_watch_s) not in (float,int)
            or not 600 <= self.min_watch_s <= 900
            or type(self.max_watch_s) not in (float,int)
            or not self.min_watch_s <= self.max_watch_s <= 900
            or type(self.max_segments) is not int or not 1<=self.max_segments<=16
            or type(self.frames_per_segment) is not int or not 10<=self.frames_per_segment<=1000
            or type(self.max_lag_ms) is not int or not 100<=self.max_lag_ms<=5000
            or type(self.source_timeout_s) not in (float,int)
            or not 5<=self.source_timeout_s<=120
            or type(self.max_storage_bytes) is not int
            or not 10_000_000<=self.max_storage_bytes<=500_000_000
            or type(self.min_free_disk_bytes) is not int
            or not 2_000_000_000<=self.min_free_disk_bytes<=50_000_000_000
            or type(self.min_free_memory_bytes) is not int
            or not 500_000_000<=self.min_free_memory_bytes<=8_000_000_000
            or type(self.per_segment_hard_timeout_s) not in (float,int)
            or not 120<=self.per_segment_hard_timeout_s<=240):
            raise ValueError("unsafe bounded soak watch policy")


def _windows_available_memory_bytes() -> int:
    if os.name != "nt":
        raise RuntimeError("timed operator watch supports authorized Windows host only")
    class MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_ulong),
            ("memory_load", ctypes.c_ulong),
            ("total_physical", ctypes.c_ulonglong),
            ("available_physical", ctypes.c_ulonglong),
            ("total_page", ctypes.c_ulonglong),
            ("available_page", ctypes.c_ulonglong),
            ("total_virtual", ctypes.c_ulonglong),
            ("available_virtual", ctypes.c_ulonglong),
            ("available_extended_virtual", ctypes.c_ulonglong),
        ]
    state=MemoryStatus()
    state.length=ctypes.sizeof(state)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        raise RuntimeError("Windows physical-memory probe unavailable")
    return int(state.available_physical)


def host_snapshot(root: Path) -> dict:
    disk=shutil.disk_usage(root)
    total_bytes=0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("symbolic link inside evidence scope refused")
        if path.is_file():
            total_bytes+=path.stat().st_size
    return {
        "available_memory_bytes":_windows_available_memory_bytes(),
        "disk_free_bytes":disk.free,
        "evidence_file_bytes":total_bytes,
    }


def _write_report(root: Path, report: dict) -> None:
    path=root/"watch_report.json"
    stage=root/"watch_report.tmp"
    if stage.is_symlink() or path.is_symlink():
        raise ValueError("report path alias refused")
    payload=json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n"
    with stage.open("w",encoding="utf-8") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())
    os.replace(stage,path)


def _valid_session(source: dict,frames: int,lag_budget:int)->bool:
    return (
        source.get("verdict")=="PASSED_FINITE_PUBLIC_IN_SESSION_SPOOL_RESEARCH_ONLY"
        and source.get("mode")=="SIMULATION"
        and source.get("derived_connection_reuse_experimental") is True
        and source.get("original_records")==frames+1
        and source.get("spool_original_cursor")==frames+1
        and source.get("receiver_receipts",0)>0
        and source.get("forward_checkpoints")==source.get("receiver_receipts")
        and source.get("projector_age_samples")==source.get("receiver_receipts")
        and source.get("projector_p99_admission_age_upper_ms") is not None
        and source.get("projector_min_headroom_ms", -1)>=0
        and source.get("max_projector_lag_ms",999999)<=lag_budget
        and source.get("queue_high_watermark",999)>=0
        and source.get("queue_high_watermark",999)<128
        and source.get("publication_in_session") is True
        and source.get("session_complete") is True
        and source.get("all_receipts_independent") is True
        and source.get("execution_started") is False
        and source.get("orders_submitted")==0
        and source.get("history_continuity_verified") is False
        and source.get("continuous_live_market") is False
    )


async def timed_watch(
    root: Path, *, policy: WatchPolicy = WatchPolicy(),
    single_trial=trial, clock=time.monotonic, metrics=portable_host_snapshot,
    diagnose_pressure: bool = False,
    reuse_producer_connections: bool = False,
    reuse_receiver_connection: bool = False,
    reuse_original_connection: bool = False,
    cooperative_reader_yield: bool = False,
) -> dict:
    policy.validate()
    if cooperative_reader_yield and not (
        reuse_original_connection and reuse_receiver_connection
        and reuse_producer_connections):
        raise ValueError("cooperative reader requires reviewed full-cache path")
    if reuse_original_connection and not (
        reuse_receiver_connection and reuse_producer_connections):
        raise ValueError("full original cache requires reviewed receiver/producer path")
    if reuse_receiver_connection and not reuse_producer_connections:
        raise ValueError("receiver cache requires producer cache")
    if (not root.is_absolute() or root.exists() or root!=root.resolve()
        or root.parent.is_symlink()):
        raise ValueError("fresh absolute non-aliased research evidence root required")
    root.mkdir(parents=True,exist_ok=False)
    start=clock()
    report={
        "schema":"econith-phase1-finite-public-watch-v1",
        "mode":"SIMULATION",
        "min_target_s":policy.min_watch_s,
        "hard_limit_s":policy.max_watch_s,
        "segments":[],"segment_boundaries_without_verified_coverage":0,
        "verdict":"STARTED_NOT_CERTIFIED",
        "target_reached":False,
        "history_continuity_verified":False,
        "continuous_live_market":False,
        "institutional_phase1_complete":False,
        "default_runtime_cutover":False,
        "orders_submitted":0,
        "diagnose_pressure":diagnose_pressure,
        "derived_connection_reuse_experimental":True,
        "producer_connection_reuse_experimental":reuse_producer_connections,
        "receiver_connection_reuse_experimental":reuse_receiver_connection,
        "original_connection_reuse_experimental":reuse_original_connection,
        "cooperative_reader_yield_experimental":cooperative_reader_yield,
    }
    _write_report(root,report)
    for number in range(1,policy.max_segments+1):
        elapsed=clock()-start
        if elapsed>=policy.min_watch_s:
            break
        remaining=policy.max_watch_s-elapsed
        if remaining<policy.per_segment_hard_timeout_s:
            report["verdict"]="FAILED_INSUFFICIENT_SAFE_TIME_BUDGET"
            break
        try:
            before=metrics(root)
            if (before["available_memory_bytes"]<policy.min_free_memory_bytes
                or before["disk_free_bytes"]<policy.min_free_disk_bytes
                or before["evidence_file_bytes"]>policy.max_storage_bytes):
                report["verdict"]="FAILED_HOST_RESOURCE_BUDGET"
                break
        except (OSError,RuntimeError,ValueError):
            report["verdict"]="FAILED_RESOURCE_TELEMETRY_UNAVAILABLE"
            break

        path=root/f"attempt_{number:03d}"
        item={
            "index":number,"status":"STARTED_NOT_CERTIFIED",
            "memory_available_before_bytes":before["available_memory_bytes"],
            "disk_free_before_bytes":before["disk_free_bytes"],
            "evidence_bytes_before":before["evidence_file_bytes"],
            "source_history_gap_to_previous_verified":number>1,
            "orders_submitted":0,
        }
        report["segments"].append(item)
        report["segment_boundaries_without_verified_coverage"]=number-1
        _write_report(root,report)
        attempt_start=clock()
        try:
            trial_args={
                "frames":policy.frames_per_segment,
                "timeout_s":policy.source_timeout_s,
                "max_lag_ms":policy.max_lag_ms,
                "reuse_derived_connections":True,
            }
            if diagnose_pressure:
                trial_args["diagnose_pressure"]=True
            for active,name in (
                (reuse_producer_connections,"reuse_producer_connections"),
                (reuse_receiver_connection,"reuse_receiver_connection"),
                (reuse_original_connection,"reuse_original_connection"),
                (cooperative_reader_yield,"cooperative_reader_yield"),
            ):
                if active:
                    trial_args[name]=True
            source=await asyncio.wait_for(
                single_trial(path,**trial_args),
                timeout=min(policy.per_segment_hard_timeout_s,remaining),
            )
            if not _valid_session(source,policy.frames_per_segment,policy.max_lag_ms):
                raise RuntimeError("bounded source/ACK/age acceptance mismatch")
            after=metrics(root)
            item.update({
                "status":"PASSED_FINITE_ONLY",
                "originals":source["original_records"],
                "receiver_acks":source["receiver_receipts"],
                "cursor":source["spool_original_cursor"],
                "checkpoints":source["forward_checkpoints"],
                "age_samples":source["projector_age_samples"],
                "age_p95_upper_ms":source["projector_p95_admission_age_upper_ms"],
                "age_p99_upper_ms":source["projector_p99_admission_age_upper_ms"],
                "max_age_ms":source["max_projector_lag_ms"],
                "min_freshness_headroom_ms":source["projector_min_headroom_ms"],
                "queue_peak":source["queue_high_watermark"],
                "pending_peak":source["max_spool_pending_originals"],
                "memory_available_after_bytes":after["available_memory_bytes"],
                "disk_free_after_bytes":after["disk_free_bytes"],
                "evidence_bytes_after":after["evidence_file_bytes"],
            })
            if (after["available_memory_bytes"]<policy.min_free_memory_bytes
                or after["disk_free_bytes"]<policy.min_free_disk_bytes
                or after["evidence_file_bytes"]>policy.max_storage_bytes):
                item["status"]="FAILED_HOST_RESOURCE_BUDGET"
                report["verdict"]="FAILED_OR_QUARANTINED"
        except Exception as exc:
            if diagnose_pressure:
                try:
                    pressure_path=path/"spool_pressure.json"
                    if (pressure_path.is_file() and not pressure_path.is_symlink()
                        and pressure_path.stat().st_size <= 100_000):
                        diagnostic=json.loads(pressure_path.read_text(encoding="utf-8"))
                        if (diagnostic.get("schema")=="econith-phase1-spool-pressure-v1"
                            and diagnostic.get("diagnostic_only_not_source_evidence") is True):
                            item["incomplete_spool_diagnostic"]={
                                "source_complete":False,
                                "raw_callback_count":diagnostic["counters"]["raw_committed"],
                                "cursor_count":diagnostic["counters"]["cursor_advanced"],
                                "checkpoints":diagnostic["counters"]["checkpoints"],
                                "windows_truncated":diagnostic["windows_truncated"],
                            }
                except (OSError,KeyError,TypeError,ValueError):
                    pass
            item.update({
                "status":"FAILED_OR_QUARANTINED",
                "failure_code":("SEGMENT_HARD_TIMEOUT" if isinstance(exc,asyncio.TimeoutError)
                                else safe_failure_code(exc)),
            })
            report["verdict"]="FAILED_OR_QUARANTINED"
        item["duration_s"]=round(clock()-attempt_start,3)
        report["watch_elapsed_s"]=round(clock()-start,3)
        _write_report(root,report)
        print(json.dumps({
            "index":number,"status":item["status"],
            "duration_s":item["duration_s"],
            "max_age_ms":item.get("max_age_ms"),
            "age_p99_upper_ms":item.get("age_p99_upper_ms"),
            "failure_code":item.get("failure_code"),
        },sort_keys=True),flush=True)
        if item["status"]!="PASSED_FINITE_ONLY":
            break

    elapsed=clock()-start
    count=sum(x["status"]=="PASSED_FINITE_ONLY" for x in report["segments"])
    report["watch_elapsed_s"]=round(elapsed,3)
    report["passed_segments"]=count
    report["failed_or_incomplete_segments"]=len(report["segments"])-count
    report["target_reached"]=(report["verdict"]=="STARTED_NOT_CERTIFIED"
                              and elapsed>=policy.min_watch_s
                              and elapsed<=policy.max_watch_s
                              and count==len(report["segments"]) and count>0)
    if report["target_reached"] and report["verdict"]=="STARTED_NOT_CERTIFIED":
        report["verdict"]="PASSED_FINITE_10MIN_MULTISEGMENT_RESEARCH_WATCH_ONLY"
    elif report["verdict"]=="STARTED_NOT_CERTIFIED":
        report["verdict"]="FAILED_TARGET_NOT_REACHED"
    _write_report(root,report)
    return report


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--accept-experimental-spool",action="store_true")
    p.add_argument("--accept-unverified-intersegment-gaps",action="store_true")
    p.add_argument("--reuse-producer-connections",action="store_true",
                   help="opt-in producer outbox/checkpoint FULL SQLite connection cache")
    p.add_argument("--reuse-receiver-connection",action="store_true",
                   help="opt-in independent HMAC receiver child SQLite cache")
    p.add_argument("--cooperative-reader-yield",action="store_true",
                   help="full-cache-only opt-in reader event-loop fairness")
    p.add_argument("--reuse-original-connection",action="store_true",
                   help="opt-in source/intent WAL FULL SQLite cache")
    p.add_argument("--diagnose-pressure",action="store_true",
                   help="record bounded per-segment pressure and stage diagnostic reports")
    p.add_argument("--output-dir",required=True,type=Path)
    p.add_argument("--min-watch-s",type=float,default=600.0)
    p.add_argument("--max-watch-s",type=float,default=900.0)
    a=p.parse_args(argv)
    if (not a.allow_public_market_data
        or not a.accept_experimental_spool
        or not a.accept_unverified_intersegment_gaps
        or (a.cooperative_reader_yield and not (
            a.reuse_original_connection and a.reuse_receiver_connection
            and a.reuse_producer_connections))
        or (a.reuse_original_connection and not (
            a.reuse_receiver_connection and a.reuse_producer_connections))
        or (a.reuse_receiver_connection and not a.reuse_producer_connections)):
        p.error("explicit consents and reviewed full-cache prerequisites required")
    policy=WatchPolicy(min_watch_s=a.min_watch_s,max_watch_s=a.max_watch_s)
    try:
        result=asyncio.run(timed_watch(
            a.output_dir,policy=policy,diagnose_pressure=a.diagnose_pressure,
            reuse_producer_connections=a.reuse_producer_connections,
            reuse_receiver_connection=a.reuse_receiver_connection,
            reuse_original_connection=a.reuse_original_connection,
            cooperative_reader_yield=a.cooperative_reader_yield,
        ))
    except Exception as exc:
        print(json.dumps({
            "verdict":"REFUSED_OR_QUARANTINED",
            "safe_failure_class":type(exc).__name__,
            "orders_submitted":0,
        },sort_keys=True),file=sys.stderr)
        return 2
    print(json.dumps({
        "verdict":result["verdict"],
        "watch_elapsed_s":result["watch_elapsed_s"],
        "passed_segments":result["passed_segments"],
        "target_reached":result["target_reached"],
        "unverified_boundaries":result["segment_boundaries_without_verified_coverage"],
        "orders_submitted":0,
    },sort_keys=True))
    return 0 if result["target_reached"] else 2


if __name__=="__main__":
    raise SystemExit(main())
