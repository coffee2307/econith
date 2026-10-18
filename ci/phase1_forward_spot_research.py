"""Manual in-session Binance PUBLIC Spot research-only forward validation.

One bounded source socket, an independent HMAC/SQLite receipt for every
admitted read-only market projection and only guarded SIMULATION subscribers.
An earlier admitted observation can NEVER certify an eventually failed
session. Never starts on app boot, CI, scheduler, or production.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_forward_verified_research import observe_forward_spot_research


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--max-frames", type=int, default=160)
    p.add_argument("--timeout-s", type=float, default=45)
    p.add_argument("--queue-capacity", type=int, default=128)
    p.add_argument("--max-book-staleness-ms", type=int, default=2500)
    p.add_argument("--max-clock-offset-ms", type=int, default=3000)
    p.add_argument("--max-clock-rtt-ms", type=int, default=2000)
    return p


def validate(args: argparse.Namespace) -> None:
    if args.allow_public_market_data is not True:
        raise PermissionError("public market-data opt-in required")
    if (
        not isinstance(args.output_dir, Path)
        or not args.output_dir.is_absolute()
        or args.output_dir.exists()
        or type(args.max_frames) is not int or not 10 <= args.max_frames <= 2000
        or type(args.timeout_s) not in (float, int)
        or not 5 <= args.timeout_s <= 120
        or type(args.queue_capacity) is not int
        or not 2 <= args.queue_capacity <= 8192
        or type(args.max_book_staleness_ms) is not int
        or not 100 <= args.max_book_staleness_ms <= 30000
        or type(args.max_clock_offset_ms) is not int
        or not 100 <= args.max_clock_offset_ms <= 10000
        or type(args.max_clock_rtt_ms) is not int
        or not 50 <= args.max_clock_rtt_ms <= 5000
    ):
        raise ValueError("invalid finite public research source policy")


async def run(args: argparse.Namespace) -> dict:
    validate(args)
    clock = await measure_public_clock(
        max_abs_offset_ms=args.max_clock_offset_ms,
        max_rtt_ms=args.max_clock_rtt_ms,
    )
    result = await observe_forward_spot_research(
        output_dir=args.output_dir,
        session_id="rec_forward_" + secrets.token_hex(12),
        allow_public_network=True,
        source="binance_public_simulation",
        max_frames=args.max_frames,
        timeout_s=args.timeout_s,
        queue_capacity=args.queue_capacity,
        max_book_staleness_ms=args.max_book_staleness_ms,
    )
    return {
        "acceptance":"RESEARCH_FORWARD_SINGLE_SESSION_ONLY",
        "clock_rtt_ms":clock.round_trip_ms,
        "clock_measured_offset_ms":clock.measured_offset_ms,
        "result":vars(result),
    }


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        print("PHASE1_FORWARD_RESEARCH_FAILED: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
