"""Finite operator matrix: THREE independent Binance public Spot spool trials.

This is a read-only SIMULATION acceptance candidate, not a continuous market
feed or certification of history across fresh WSS sessions. Each segment
performs its own HTTPS clock preflight, fresh public WSS/REST, isolated
source/outbox/receiver/derived/checkpoint/cursor; no retries hiding failures.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from ci.phase1_public_live_spool_trial import run
from ci.phase1_public_spool_timed_watch import host_snapshot
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code


def _safe_store(path:Path,report:dict)->None:
    # The operator root is fresh and isolated; no source ledger rewritten.
    path.write_text(
        json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n",
        encoding="utf-8",
    )


async def matrix(root:Path,*,frames:int,max_lag_ms:int,
                 reuse_derived_connections:bool=False,
                 reuse_producer_connections:bool=False,
                 reuse_receiver_connection:bool=False,
                 reuse_original_connection:bool=False,
                 diagnose_pressure:bool=False,
                 resource_guard:bool=False)->dict:
    if (reuse_original_connection and not (
        reuse_receiver_connection and reuse_producer_connections
        and reuse_derived_connections)):
        raise ValueError("original cache requires all reviewed caches")
    if (reuse_receiver_connection and not (
        reuse_producer_connections and reuse_derived_connections)):
        raise ValueError("receiver cache requires producer and derived cache")
    if reuse_producer_connections and not reuse_derived_connections:
        raise ValueError("producer cache requires derived cache")
    started=time.monotonic()
    results=[]
    for i in range(1,4):
        path=root/f"attempt_{i:03d}"
        item={"iteration":i, "status":"FAILED_OR_QUARANTINED_SOURCE"}
        try:
            # No market session starts below the agreed host floor.
            if shutil.disk_usage(root).free < 2_000_000_000:
                raise RuntimeError("public spool disk budget insufficient")
            if resource_guard:
                before=host_snapshot(root)
                if (before["available_memory_bytes"]<1_500_000_000
                    or before["disk_free_bytes"]<2_000_000_000
                    or before["evidence_file_bytes"]>500_000_000):
                    raise RuntimeError("public spool host memory/evidence budget insufficient")
                item["memory_free_before_bytes"]=before["available_memory_bytes"]
                item["evidence_before_bytes"]=before["evidence_file_bytes"]
            public_run_args = dict(frames=frames,timeout_s=120.0,
                                   max_lag_ms=max_lag_ms)
            for enabled,key in (
                (reuse_derived_connections,"reuse_derived_connections"),
                (reuse_producer_connections,"reuse_producer_connections"),
                (reuse_receiver_connection,"reuse_receiver_connection"),
                (reuse_original_connection,"reuse_original_connection"),
                (diagnose_pressure,"diagnose_pressure"),
            ):
                if enabled:
                    public_run_args[key]=True
            passed=await run(path,**public_run_args)
            if resource_guard:
                after=host_snapshot(root)
                if (after["available_memory_bytes"]<1_500_000_000
                    or after["disk_free_bytes"]<2_000_000_000
                    or after["evidence_file_bytes"]>500_000_000):
                    raise RuntimeError("public spool post-session resource budget insufficient")
            else:
                after=None
            item={
                "iteration":i,"status":"PASSED",
                "originals":passed["original_records"],
                "receiver_acks":passed["receiver_receipts"],
                "checkpoints":passed["forward_checkpoints"],
                "cursor":passed["spool_original_cursor"],
                "queue_peak":passed["queue_high_watermark"],
                "max_lag_ms":passed["max_projector_lag_ms"],
                "max_pending_originals":passed["max_spool_pending_originals"],
                "publication_in_session":passed["publication_in_session"],
                "projector_age_samples":passed.get("projector_age_samples"),
                "admission_age_p50_upper_ms":passed.get("projector_p50_admission_age_upper_ms"),
                "admission_age_p95_upper_ms":passed.get("projector_p95_admission_age_upper_ms"),
                "admission_age_p99_upper_ms":passed.get("projector_p99_admission_age_upper_ms"),
                "min_freshness_headroom_ms":passed.get("projector_min_headroom_ms"),
                "orders_submitted":0,
                "derived_cache_opt_in":reuse_derived_connections,
                "producer_cache_opt_in":reuse_producer_connections,
                "receiver_cache_opt_in":reuse_receiver_connection,
                "original_cache_opt_in":reuse_original_connection,
                "pressure_diagnostic_opt_in":diagnose_pressure,
            }
            if after is not None:
                item["memory_free_after_bytes"]=after["available_memory_bytes"]
                item["evidence_after_bytes"]=after["evidence_file_bytes"]
        except Exception as exc:
            item["failure_code"]=safe_failure_code(exc)
            item["orders_submitted"]=0
            # A failed original is ALWAYS quarantined. This diagnostic can
            # explain age distribution but can never change its verdict.
            diag=path/"projector_lag.json"
            try:
                if diag.is_file() and not diag.is_symlink() and diag.stat().st_size<=32_768:
                    age=json.loads(diag.read_text(encoding="utf-8"))
                    if (age.get("schema")=="econith-phase1-projector-age-histogram-v1"
                        and age.get("diagnostic_only_not_source_evidence") is True):
                        item["incomplete_projector_diagnostic"]={
                            "source_complete":False,
                            "age_samples":age.get("age_samples"),
                            "p95_age_upper_ms":age.get("p95_age_upper_ms"),
                            "p99_age_upper_ms":age.get("p99_age_upper_ms"),
                            "max_pending_originals":age.get("max_pending_originals"),
                        }
            except (OSError,ValueError,TypeError,UnicodeError):
                pass
        results.append(item)
        print(json.dumps(item,sort_keys=True),flush=True)
    passed_count=sum(item["status"]=="PASSED" for item in results)
    report={
        "schema":"econith-phase1-public-spool-matrix-v1",
        "mode":"SIMULATION",
        "attempts":3, "passed":passed_count,
        "failed":3-passed_count,
        "frames_per_attempt":frames,
        "lag_budget_ms":max_lag_ms,
        "derived_connection_reuse_experimental":reuse_derived_connections,
        "producer_connection_reuse_experimental":reuse_producer_connections,
        "receiver_connection_reuse_experimental":reuse_receiver_connection,
        "original_connection_reuse_experimental":reuse_original_connection,
        "pressure_diagnostic_opt_in":diagnose_pressure,
        "host_resource_guard":resource_guard,
        "duration_s":round(time.monotonic()-started,3),
        "verdict":("PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY"
                   if passed_count==3 and frames==1000
                   else "FAILED_OR_BOUNDED_RESEARCH_ONLY"),
        "segments":results,
        "history_gap_free_verified":False,
        "continuous_market_feed_certified":False,
        "default_runtime_verified_only_cutover":False,
        "institutional_phase1_complete":False,
        "orders_submitted":0,
    }
    _safe_store(root/"matrix_report.json",report)
    return report


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--accept-experimental-spool",action="store_true")
    p.add_argument("--reuse-original-connection",action="store_true",
                   help="explicit finite original+intent WAL/FULL session cache")
    p.add_argument("--reuse-receiver-connection",action="store_true",
                   help="explicit persistent HMAC child SQLite cache")
    p.add_argument("--reuse-producer-connections",action="store_true",
                   help="explicit outbox/checkpoint SQLite session cache")
    p.add_argument("--diagnose-pressure",action="store_true",
                   help="bounded per-window FIFO/receiver/ingress diagnostic")
    p.add_argument("--reuse-derived-connections",action="store_true",
                   help="optional reviewed A/B mode; does not alter production runtime")
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--frames",type=int,default=1000)
    p.add_argument("--max-lag-ms",type=int,default=5000)
    a=p.parse_args(argv)
    if (not a.allow_public_market_data or not a.accept_experimental_spool
        or (a.reuse_original_connection and not (
            a.reuse_receiver_connection and a.reuse_producer_connections
            and a.reuse_derived_connections))
        or (a.reuse_receiver_connection and not (
            a.reuse_producer_connections and a.reuse_derived_connections))
        or (a.reuse_producer_connections and not a.reuse_derived_connections)
        or not a.output_dir.is_absolute() or a.output_dir.exists()
        or a.output_dir!=a.output_dir.resolve()
        or type(a.frames) is not int or not 10<=a.frames<=1000
        or type(a.max_lag_ms) is not int or not 100<=a.max_lag_ms<=5000):
        p.error("unsafe or unapproved public 3-segment spool policy")
    a.output_dir.mkdir(parents=True,exist_ok=False)
    report=asyncio.run(matrix(
        a.output_dir,frames=a.frames,max_lag_ms=a.max_lag_ms,
        reuse_derived_connections=a.reuse_derived_connections,
        reuse_producer_connections=a.reuse_producer_connections,
        reuse_receiver_connection=a.reuse_receiver_connection,
        reuse_original_connection=a.reuse_original_connection,
        diagnose_pressure=a.diagnose_pressure,
        resource_guard=True,
    ))
    print(json.dumps({
        "verdict":report["verdict"],"passed":report["passed"],
        "failed":report["failed"],"duration_s":report["duration_s"],
        "orders_submitted":0,
    },sort_keys=True),flush=True)
    return 0 if report["passed"]==3 else 2


if __name__=="__main__":
    raise SystemExit(main())
