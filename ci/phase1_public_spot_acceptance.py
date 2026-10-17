"""Manually invoked Binance Spot public-data acceptance probe (NEVER startup).

Usage: python -m ci.phase1_public_spot_acceptance --allow-public-market-data
       --output-dir <operator-chosen-directory>

No API keys, orders, authenticated user endpoints or live capital. CLI refuses
any network IO unless the explicit consent flag is present. It verifies
server clock plausibility, saves original public capture records, replays them,
and reports only observed local facts. Snapshot/WS/TLS are verified by the
underlying read-only adapter, not by any exchange message signature.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController
from infrastructure.spot_worker_fence import OfflineSpotWorkerFence

_TIME_ENDPOINT = "https://data-api.binance.vision/api/v3/time"


@dataclass(frozen=True)
class ObservedClock:
    server_time_ms: int
    measured_offset_ms: int
    round_trip_ms: int
    estimated_uncertainty_ms: int


async def measure_public_clock(
    *, max_abs_offset_ms: int, max_rtt_ms: int,
    wall_ms=None, monotonic_ms=None,
    client_factory=httpx.AsyncClient,
) -> ObservedClock:
    """RTT/2 is a rough observation bound, *not* signed/NTP-certified time."""
    wall = wall_ms or (lambda: time.time_ns() // 1_000_000)
    monotonic = monotonic_ms or (lambda: time.monotonic_ns() // 1_000_000)
    start_wall, start_mono = wall(), monotonic()
    async with client_factory(
        verify=True, trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(8.0),
        headers={"Accept-Encoding": "identity"},
    ) as client:
        response = await client.get(_TIME_ENDPOINT)
        response.raise_for_status()
        payload = response.json()
    end_mono, end_wall = monotonic(), wall()
    if (
        type(payload) is not dict
        or type(payload.get("serverTime")) is not int
        or payload["serverTime"] <= 0
    ):
        raise RuntimeError("invalid public exchange clock response")
    rtt = end_mono - start_mono
    if rtt < 0 or rtt > max_rtt_ms or end_wall < start_wall:
        raise RuntimeError("unreliable public exchange clock RTT or local clock regression")
    offset = payload["serverTime"] - ((start_wall + end_wall) // 2)
    if abs(offset) > max_abs_offset_ms:
        raise RuntimeError("local clock offset exceeds explicit admission threshold")
    return ObservedClock(
        payload["serverTime"], offset, rtt,
        (rtt + 1) // 2,
    )


def args_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-public-market-data", action="store_true",
        help="mandatory opt-in; reads only Binance public Spot TLS/WSS and GET",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbol", choices=["BTCUSDT"], default="BTCUSDT")
    parser.add_argument("--max-frames", type=int, default=500)
    parser.add_argument("--timeout-s", type=float, default=45.0)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--max-clock-offset-ms", type=int, default=3000)
    parser.add_argument("--max-clock-rtt-ms", type=int, default=2000)
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.allow_public_market_data is not True:
        raise PermissionError(
            "explicit --allow-public-market-data is required; no network request sent"
        )
    if (
        not 10 <= args.max_frames <= 2500
        or not 1 <= args.max_attempts <= 3
        or not 5 <= args.timeout_s <= 120
        or not 100 <= args.max_clock_offset_ms <= 10000
        or not 50 <= args.max_clock_rtt_ms <= 5000
    ):
        raise ValueError("bounded market capture and clock parameters are required")
    if not args.output_dir.is_absolute():
        raise ValueError("output directory must be an absolute path")


async def run(args: argparse.Namespace) -> dict:
    validate_args(args)
    # No output or external network access before the explicit flag/limits.
    clock = await measure_public_clock(
        max_abs_offset_ms=args.max_clock_offset_ms,
        max_rtt_ms=args.max_clock_rtt_ms,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    db = args.output_dir / "original_binance_spot_public.sqlite"
    journal = OfflineSpotCaptureJournal(
        db, mode_provider=lambda: QuantMode.SIMULATION,
    )
    adapter = OptInSpotIngressAdapter(journal, symbol=args.symbol)
    fence = OfflineSpotWorkerFence(
        journal, controller_id="spot_public_acceptance",
        lease_ms=30_000,
    )
    controller = OfflineSpotReconnectController(
        journal, controller_id="spot_public_acceptance",
        symbol=args.symbol, worker_fence=fence,
    )
    outcome = await controller.run_fenced_public(
        adapter, allow_network=True,
        holder_id="manual_public_acceptance",
        max_attempts=args.max_attempts, backoff_s=1.0,
        max_frames=args.max_frames, timeout_s=args.timeout_s,
        heartbeat_interval_s=4.0,
    )
    report = {
        "scope": "PUBLIC_BINANCE_SPOT_READ_ONLY",
        "symbol": args.symbol,
        "clock_check": asdict(clock),
        "status": outcome.status,
        "history_continuity_verified": False,
        "attempts": [asdict(item) for item in controller.attempts()],
        "outages": [asdict(item) for item in controller.outages()],
        "evidence_db": str(db),
        "book_last_update_id": outcome.latest_book_update_id,
        "test_only": True,
        "real_trading": False,
    }
    if outcome.status != "RECOVERED" or outcome.last_session_id is None:
        report["acceptance"] = "FAILED_NO_SYNCED_PUBLIC_CAPTURE"
    else:
        observed = OfflineSpotCaptureReplayer(journal).replay(
            outcome.last_session_id,
            as_of_ms=max(
                attempt.finished_at_ms for attempt in controller.attempts()
                if attempt.session_id == outcome.last_session_id
            ),
        )
        report["capture"] = {
            "session_id": observed.session_id,
            "total_original_frames": observed.total_records,
            "agg_trades": len(observed.trades),
            "depth_status": observed.depth_status,
            "last_depth_update_id": observed.book.last_update_id if observed.book else None,
        }
        report["acceptance"] = (
            "PASSED_OBSERVED_BOUNDED_SESSION"
            if observed.book is not None and observed.total_records > 1
            else "FAILED_INCOMPLETE_CAPTURE"
        )
    report_path = args.output_dir / "phase1_spot_public_acceptance.json"
    report_path.write_text(
        json.dumps(report, sort_keys=True, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    options = args_parser().parse_args(argv)
    try:
        report = asyncio.run(run(options))
        print(json.dumps(report, sort_keys=True))
        return 0 if report["acceptance"] == "PASSED_OBSERVED_BOUNDED_SESSION" else 2
    except Exception as exc:
        # No tokens/URLs/passwords or external response bodies in human logs.
        print("PHASE1_PUBLIC_CAPTURE_FAILED: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
