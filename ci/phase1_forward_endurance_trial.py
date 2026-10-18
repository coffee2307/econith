"""Manually opted-in, FINITE public Binance Spot forward endurance trial.

Records durable fresh-source attempts and explicit outage windows, with
original/outbox/HMAC receiver and research observer proof per accepted round.
Not a continuous gap-free stream, not execution, not a daemon or a production
default market-data source.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_forward_supervisor import run_forward_research_supervisor


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--attempts",type=int,default=3)
    p.add_argument("--max-frames",type=int,default=160)
    p.add_argument("--timeout-s",type=float,default=45)
    p.add_argument("--pause-s",type=float,default=1)
    p.add_argument("--max-storage-mb",type=int,default=100)
    p.add_argument("--max-book-staleness-ms",type=int,default=2500)
    p.add_argument("--max-clock-offset-ms",type=int,default=3000)
    p.add_argument("--max-clock-rtt-ms",type=int,default=2000)
    return p


def validate(args: argparse.Namespace) -> None:
    if args.allow_public_market_data is not True:
        raise PermissionError("explicit public research opt-in required")
    if (
        not isinstance(args.output_dir,Path)
        or not args.output_dir.is_absolute() or args.output_dir.exists()
        or type(args.attempts) is not int or not 1 <= args.attempts <= 16
        or type(args.max_frames) is not int or not 10 <= args.max_frames <= 2000
        or args.attempts*args.max_frames>32000
        or type(args.timeout_s) not in (int,float) or not 5 <= args.timeout_s <= 120
        or type(args.pause_s) not in (int,float) or not 0 <= args.pause_s <= 30
        or type(args.max_storage_mb) is not int or not 2 <= args.max_storage_mb <= 2000
        or type(args.max_book_staleness_ms) is not int
        or not 100 <= args.max_book_staleness_ms <= 30000
        or type(args.max_clock_offset_ms) is not int
        or not 100 <= args.max_clock_offset_ms <= 10000
        or type(args.max_clock_rtt_ms) is not int
        or not 50 <= args.max_clock_rtt_ms <= 5000
    ):
        raise ValueError("invalid bounded public endurance policy")


async def run(args: argparse.Namespace) -> dict:
    validate(args)
    async def clock_check():
        await measure_public_clock(
            max_abs_offset_ms=args.max_clock_offset_ms,
            max_rtt_ms=args.max_clock_rtt_ms,
        )
        return True
    result=await run_forward_research_supervisor(
        output_dir=args.output_dir,allow_public_network=True,
        attempts=args.attempts,max_frames=args.max_frames,
        timeout_s=args.timeout_s,pause_s=args.pause_s,
        max_total_storage_bytes=args.max_storage_mb*1_000_000,
        max_book_staleness_ms=args.max_book_staleness_ms,
        clock_check=clock_check,
    )
    return asdict(result)


def main(argv: list[str] | None = None) -> int:
    args=parser().parse_args(argv)
    try:
        status=asyncio.run(run(args))
    except Exception as exc:
        # Never output potential URL, payload, secrets or private SQL details.
        print("PHASE1_FORWARD_ENDURANCE_FAILED: "+type(exc).__name__,
              file=sys.stderr)
        return 2
    print(json.dumps(status,sort_keys=True,allow_nan=False))
    if (
        status["incomplete_attempts"] or status["failed_sessions"]
        or not status["accepted_sessions"] or status["open_outages"]
    ):
        return 2
    return 0  # finite trial passed; history_continuity_verified stays False


if __name__=="__main__":
    raise SystemExit(main())
