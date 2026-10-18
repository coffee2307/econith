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
from infrastructure.spot_single_socket_watch import run_single_socket_forward_watch


def parser() -> argparse.ArgumentParser:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--attempts",type=int,default=3)
    p.add_argument("--watch-hours",type=float,default=None,
                   help="Operator-duration trial (max 72h); use with --persistent-receiver")
    p.add_argument("--max-frames",type=int,default=160)
    p.add_argument("--timeout-s",type=float,default=45)
    p.add_argument("--pause-s",type=float,default=1)
    p.add_argument("--max-storage-mb",type=int,default=100)
    p.add_argument("--max-book-staleness-ms",type=int,default=2500)
    p.add_argument("--max-clock-offset-ms",type=int,default=3000)
    p.add_argument("--max-clock-rtt-ms",type=int,default=2000)
    p.add_argument("--ws-idle-timeout-s",type=float,default=30.0)
    p.add_argument("--persistent-receiver",action="store_true")
    p.add_argument("--fresh-socket-per-segment",action="store_true",
                   help="Discard public WSS after every bounded attempt; no claimed history continuity")
    p.add_argument("--single-socket",action="store_true",
                   help="Reuse one public TLS socket across finite verified segments")
    return p


def validate(args: argparse.Namespace) -> None:
    if args.allow_public_market_data is not True:
        raise PermissionError("explicit public research opt-in required")
    if (
        not isinstance(args.output_dir,Path)
        or not args.output_dir.is_absolute() or args.output_dir.exists()
        or type(args.attempts) is not int
        or not 1 <= args.attempts <= (4096 if args.watch_hours is not None else 16)
        or type(args.max_frames) is not int or not 10 <= args.max_frames <= 2000
        or args.attempts*args.max_frames>(
            4_096_000 if args.watch_hours is not None else 32000)
        or (args.watch_hours is not None and (
            type(args.watch_hours) is not float
            or not (5/3600) <= args.watch_hours <= 72
            or not args.persistent_receiver))
        or type(args.timeout_s) not in (int,float) or not 5 <= args.timeout_s <= 120
        or type(args.pause_s) not in (int,float) or not 0 <= args.pause_s <= 30
        or type(args.max_storage_mb) is not int or not 2 <= args.max_storage_mb <= 2000
        or type(args.max_book_staleness_ms) is not int
        or not 100 <= args.max_book_staleness_ms <= 30000
        or type(args.max_clock_offset_ms) is not int
        or not 100 <= args.max_clock_offset_ms <= 10000
        or type(args.ws_idle_timeout_s) is not float
        or not 0.1 <= args.ws_idle_timeout_s <= 120
        or type(args.max_clock_rtt_ms) is not int
        or not 50 <= args.max_clock_rtt_ms <= 5000
        or (args.single_socket and not args.persistent_receiver)
        or (args.fresh_socket_per_segment and not args.single_socket)
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
    if args.single_socket:
        pilot=await run_single_socket_forward_watch(
            output_dir=args.output_dir,allow_public_market_data=True,
            attempts=args.attempts,max_frames=args.max_frames,
            timeout_s=args.timeout_s,
            max_total_storage_bytes=args.max_storage_mb*1_000_000,
            max_book_staleness_ms=args.max_book_staleness_ms,
            public_ws_idle_timeout_s=args.ws_idle_timeout_s,
            watch_duration_s=(args.watch_hours*3600 if args.watch_hours is not None else None),
            clock_check=clock_check,
            fresh_socket_per_segment=args.fresh_socket_per_segment,
        )
        result=asdict(pilot.supervisor)
        result.update({
            "public_socket_starts":pilot.public_socket_starts,
            "successful_socket_reuses":pilot.successful_socket_reuses,
            "discarded_broken_sockets":pilot.discarded_broken_sockets,
            "public_tls_lease_established_by_trusted_connector":
                pilot.public_tls_lease_established_by_trusted_connector,
            "no_unobserved_market_gaps_certified":False,
        })
        return result
    result=await run_forward_research_supervisor(
        output_dir=args.output_dir,allow_public_network=True,
        attempts=args.attempts,max_frames=args.max_frames,
        timeout_s=args.timeout_s,pause_s=args.pause_s,
        max_total_storage_bytes=args.max_storage_mb*1_000_000,
        max_book_staleness_ms=args.max_book_staleness_ms,
        persistent_receiver=args.persistent_receiver,
        public_ws_idle_timeout_s=args.ws_idle_timeout_s,
        watch_duration_s=(args.watch_hours*3600 if args.watch_hours is not None else None),
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
        (args.watch_hours is not None and not status["watch_target_reached"])
        or status["incomplete_attempts"] or status["failed_sessions"]
        or not status["accepted_sessions"] or status["open_outages"]
    ):
        return 2
    return 0  # finite trial passed; history_continuity_verified stays False


if __name__=="__main__":
    raise SystemExit(main())
