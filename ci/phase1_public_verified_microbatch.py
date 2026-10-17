"""Explicit Binance Spot public market-data-only verified research microbatch CLI.

This is a finite number of sealed/replayed public sessions, not a gapless,
always-on live feed. Never started by FastAPI main, CI, deployment or scheduler.
The only network calls are pinned public TLS WSS, GET /api/v3/depth and
GET /api/v3/time, and only after explicit --allow-public-market-data.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_public_verified_microbatch import (
    observe_verified_public_batches,
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--symbol", choices=["BTCUSDT"], default="BTCUSDT")
    p.add_argument("--cycles", type=int, default=2)
    p.add_argument("--max-frames", type=int, default=160)
    p.add_argument("--timeout-s", type=float, default=45)
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--max-clock-offset-ms", type=int, default=3000)
    p.add_argument("--max-clock-rtt-ms", type=int, default=2000)
    return p


def validate(args: argparse.Namespace) -> None:
    if args.allow_public_market_data is not True:
        raise PermissionError("explicit public Binance market-data opt-in required")
    if (
        not args.output_dir.is_absolute()
        or type(args.cycles) is not int or not 1 <= args.cycles <= 4
        or type(args.max_frames) is not int or not 10 <= args.max_frames <= 2000
        or type(args.max_attempts) is not int or not 1 <= args.max_attempts <= 3
        or type(args.timeout_s) not in (int, float) or not 5 <= args.timeout_s <= 120
        or type(args.max_clock_offset_ms) is not int
        or not 100 <= args.max_clock_offset_ms <= 10000
        or type(args.max_clock_rtt_ms) is not int
        or not 50 <= args.max_clock_rtt_ms <= 5000
    ):
        raise ValueError("invalid bounded public research admission limits")
    if args.output_dir.exists():
        raise FileExistsError("a new directory is required; do not reuse prior research evidence")


async def run(args: argparse.Namespace) -> dict:
    validate(args)
    clock = await measure_public_clock(
        max_abs_offset_ms=args.max_clock_offset_ms,
        max_rtt_ms=args.max_clock_rtt_ms,
    )
    result = await observe_verified_public_batches(
        output_dir=args.output_dir, allow_network=True,
        symbol=args.symbol, cycles=args.cycles, max_frames=args.max_frames,
        timeout_s=args.timeout_s, max_attempts=args.max_attempts,
    )
    result["coarse_public_clock_check"] = {
        "round_trip_ms":clock.round_trip_ms,
        "measured_offset_ms":clock.measured_offset_ms,
        "estimated_uncertainty_ms":clock.estimated_uncertainty_ms,
    }
    (args.output_dir / "phase1_verified_public_microbatch.json").write_text(
        json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    opts = parser().parse_args(argv)
    try:
        report = asyncio.run(run(opts))
        print(json.dumps(report,sort_keys=True,allow_nan=False))
        return 0 if report["acceptance"] == "PASSED_BOUNDED_PUBLIC_VERIFIED_MICROBATCH_RESEARCH" else 2
    except Exception as exc:
        # Network exceptions/DB errors could include private URLs and paths.
        print("PHASE1_VERIFIED_PUBLIC_MICROBATCH_FAILED: " + type(exc).__name__,
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
