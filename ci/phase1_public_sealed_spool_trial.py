"""Manually opt in to ONE finite read-only Binance Spot source-first session.

Captures all raw first, seals/checks the source, then emits HISTORICAL research
replay through separately authenticated durable receiver, observers and
checkpoints. This is not continuous live ingress or broker/production access.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_bounded_durable_spool import (
    capture_then_replay_sealed_spool,
)
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code


async def run(*, output_dir: Path, frames: int, timeout_s: float) -> dict:
    await measure_public_clock(max_abs_offset_ms=3000, max_rtt_ms=2000)
    report=await capture_then_replay_sealed_spool(
        output_dir=output_dir, session_id="rec_spool_"+__import__("secrets").token_hex(10),
        allow_public_network=True, max_frames=frames, timeout_s=timeout_s,
        max_source_raw_bytes=200_000_000, queue_capacity=128,
    )
    outcome=asdict(report)
    outcome.update({
        "verdict":"PASSED_FINITE_SEALED_PUBLIC_RESEARCH_ONLY",
        "exchange_source_history_complete":False,
        "operator_not_institutional_phase1_certification":True,
        "default_app_cutover":False,
    })
    return outcome


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--frames",type=int,default=1000)
    p.add_argument("--timeout-s",type=float,default=120.0)
    args=p.parse_args(argv)
    if args.allow_public_market_data is not True:
        p.error("explicit read-only public market-data opt-in required")
    if (not args.output_dir.is_absolute()
        or args.output_dir.exists()
        or args.output_dir != args.output_dir.resolve()
        or type(args.frames) is not int or not 10 <= args.frames <= 2000
        or type(args.timeout_s) is not float
        or not 5 <= args.timeout_s <= 120):
        p.error("unsafe, reused or unbounded operator trial")
    try:
        report=asyncio.run(run(
            output_dir=args.output_dir,frames=args.frames,
            timeout_s=args.timeout_s,
        ))
    except Exception as exc:
        print(json.dumps({
            "verdict":"FAILED_OR_QUARANTINED_SOURCE",
            "error_class":type(exc).__name__,
            "safe_failure_code":safe_failure_code(exc),
            "source_coverage_certified":False,
            "orders_submitted":0,
        }),file=sys.stderr)
        return 2
    print(json.dumps(report,sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
