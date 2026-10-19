"""Explicit opt-in supervised, FINITE public Spot verified research soak.

This CLI has no scheduler/daemon and is NEVER imported by default main.py.
Each session is closed and independently HMAC source/receiver ACK-verified
before research publication. Gaps between sessions are ALWAYS unobserved.
Public endpoints are TLS/WSS market-data only, no API key / account / orders.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_verified_soak import run_verified_spot_soak


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--max-frames", type=int, default=160)
    p.add_argument("--timeout-s", type=float, default=45)
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--pause-s", type=float, default=0)
    p.add_argument("--max-clock-offset-ms", type=int, default=3000)
    p.add_argument("--max-clock-rtt-ms", type=int, default=2000)
    p.add_argument("--resume", action="store_true")
    return p


def validate(args: argparse.Namespace) -> None:
    # Validate BEFORE public time endpoint access. No execution route here.
    if args.allow_public_market_data is not True:
        raise PermissionError("public market-data research needs explicit opt-in")
    if (
        not args.output_dir.is_absolute()
        or type(args.rounds) is not int or not 1 <= args.rounds <= 24
        or type(args.max_frames) is not int or not 10 <= args.max_frames <= 2000
        or args.rounds * args.max_frames > 48_000
        or type(args.max_attempts) is not int or not 1 <= args.max_attempts <= 3
        or type(args.timeout_s) not in (float, int) or not 5 <= args.timeout_s <= 120
        or type(args.pause_s) not in (float, int) or not 0 <= args.pause_s <= 60
        or type(args.max_clock_offset_ms) is not int
        or not 100 <= args.max_clock_offset_ms <= 10000
        or type(args.max_clock_rtt_ms) is not int
        or not 50 <= args.max_clock_rtt_ms <= 5000
        or (args.resume and not args.output_dir.is_dir())
        or (not args.resume and args.output_dir.exists())
    ):
        raise ValueError("invalid finite soak resource, clock or destination policy")


async def run(args: argparse.Namespace) -> dict:
    validate(args)

    async def checked_public_clock() -> bool:
        await measure_public_clock(
            max_abs_offset_ms=args.max_clock_offset_ms,
            max_rtt_ms=args.max_clock_rtt_ms,
        )
        return True

    return await run_verified_spot_soak(
        output_dir=args.output_dir, allow_network=True,
        rounds=args.rounds, max_frames=args.max_frames,
        timeout_s=args.timeout_s, max_attempts=args.max_attempts,
        pause_s=args.pause_s, resume=args.resume,
        clock_check=checked_public_clock,
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        # Fail-closed. No private paths, raw payload, credentials, or SQL.
        print("PHASE1_VERIFIED_SOAK_FAILED: " + type(exc).__name__, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result.get("status") == "COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
