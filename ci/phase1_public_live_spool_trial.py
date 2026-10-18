"""ONE manually authorized, finite public Spot WSS in-session spool test.

Experimental read-only SIMULATION. Requires trusted public clock preflight,
fresh output path, fixed 128 ingress, bounded source->publication lag. A
failure quarantines the entire source; no retries, no orders, no default-app
cutover or claims of continuous uninterrupted exchange market coverage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from dataclasses import asdict
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code
from infrastructure.spot_forward_verified_research import observe_forward_spot_research


async def run(root:Path,*,frames:int,timeout_s:float,max_lag_ms:int)->dict:
    await measure_public_clock(
        max_abs_offset_ms=3000,max_rtt_ms=2000,
    )
    result=await observe_forward_spot_research(
        output_dir=root,session_id="rec_live_spool_"+secrets.token_hex(8),
        allow_public_network=True,source="binance_public_simulation",
        max_frames=frames,timeout_s=timeout_s,queue_capacity=128,
        max_book_staleness_ms=2500,persistent_receiver=True,
        spooled_in_session=True,allow_experimental_public_spool=True,
        max_projector_lag_ms=max_lag_ms,max_pending_originals=512,
        max_source_raw_bytes=200_000_000,
    )
    if (not result.session_complete or not result.publication_in_session
        or not result.all_receipts_independent or result.orders_submitted
        or result.spool_original_cursor!=result.original_records
        or result.queue_high_watermark>=128
        or result.max_projector_lag_ms>max_lag_ms):
        raise RuntimeError("bounded public spool research proof incomplete")
    return {
        **asdict(result),
        "verdict":"PASSED_FINITE_PUBLIC_IN_SESSION_SPOOL_RESEARCH_ONLY",
        "history_gap_free_verified":False,
        "default_market_ingress_cutover":False,
        "institutional_phase1_complete":False,
    }


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--accept-experimental-spool",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--frames",type=int,default=1000)
    p.add_argument("--timeout-s",type=float,default=120.0)
    p.add_argument("--max-lag-ms",type=int,default=5000)
    args=p.parse_args(argv)
    if (not args.allow_public_market_data
        or not args.accept_experimental_spool
        or not args.output_dir.is_absolute()
        or args.output_dir.exists()
        or args.output_dir!=args.output_dir.resolve()
        or type(args.frames) is not int or not 10<=args.frames<=1000
        or type(args.timeout_s) is not float or not 5<=args.timeout_s<=120
        or type(args.max_lag_ms) is not int or not 100<=args.max_lag_ms<=5000):
        p.error("refusing unsafe experimental public spool trial")
    try:
        result=asyncio.run(run(args.output_dir,frames=args.frames,
                               timeout_s=args.timeout_s,
                               max_lag_ms=args.max_lag_ms))
    except Exception as exc:
        print(json.dumps({
            "verdict":"FAILED_OR_QUARANTINED_SOURCE",
            "failure_code":safe_failure_code(exc),
            "execution_started":False,"orders_submitted":0,
            "source_history_continuity_verified":False,
        },sort_keys=True),file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
