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
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code


def _safe_store(path:Path,report:dict)->None:
    # The operator root is fresh and isolated; no source ledger rewritten.
    path.write_text(
        json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n",
        encoding="utf-8",
    )


async def matrix(root:Path,*,frames:int,max_lag_ms:int)->dict:
    started=time.monotonic()
    results=[]
    for i in range(1,4):
        path=root/f"attempt_{i:03d}"
        item={"iteration":i, "status":"FAILED_OR_QUARANTINED_SOURCE"}
        try:
            # Check disk remaining before every fresh session, fail closed.
            if shutil.disk_usage(root).free < 2_000_000_000:
                raise RuntimeError("public spool disk budget insufficient")
            passed=await run(path,frames=frames,timeout_s=120.0,
                             max_lag_ms=max_lag_ms)
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
                "orders_submitted":0,
            }
        except Exception as exc:
            item["failure_code"]=safe_failure_code(exc)
            item["orders_submitted"]=0
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
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--frames",type=int,default=1000)
    p.add_argument("--max-lag-ms",type=int,default=5000)
    a=p.parse_args(argv)
    if (not a.allow_public_market_data or not a.accept_experimental_spool
        or not a.output_dir.is_absolute() or a.output_dir.exists()
        or a.output_dir!=a.output_dir.resolve()
        or type(a.frames) is not int or not 10<=a.frames<=1000
        or type(a.max_lag_ms) is not int or not 100<=a.max_lag_ms<=5000):
        p.error("unsafe or unapproved public 3-segment spool policy")
    a.output_dir.mkdir(parents=True,exist_ok=False)
    report=asyncio.run(matrix(
        a.output_dir,frames=a.frames,max_lag_ms=a.max_lag_ms,
    ))
    print(json.dumps({
        "verdict":report["verdict"],"passed":report["passed"],
        "failed":report["failed"],"duration_s":report["duration_s"],
        "orders_submitted":0,
    },sort_keys=True),flush=True)
    return 0 if report["passed"]==3 else 2


if __name__=="__main__":
    raise SystemExit(main())
