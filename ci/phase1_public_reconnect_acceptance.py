"""Manual two-session public Spot reconnect acceptance with a forced local WS cut.

Research-only, no orders or credentials. The FIRST actual public WSS session
deliberately aborts after N frames; a SECOND fresh WSS + REST snapshot must
synchronize. A successful test proves bounded reconnect control and durable
outage bookkeeping, NOT the restoration of events missing during the cut.

No start-up hook, no schedule, no daemon, no production implementation.
Explicit --allow-public-market-data is mandatory before any network access.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from core.mode import QuantMode
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController
from infrastructure.spot_worker_fence import OfflineSpotWorkerFence
from ci.phase1_public_spot_acceptance import measure_public_clock


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--symbol", choices=["BTCUSDT"], default="BTCUSDT")
    p.add_argument("--interrupt-after-frames", type=int, default=80)
    p.add_argument("--max-frames", type=int, default=500)
    p.add_argument("--timeout-s", type=float, default=45.0)
    p.add_argument("--max-clock-offset-ms", type=int, default=3000)
    p.add_argument("--max-clock-rtt-ms", type=int, default=2000)
    return p


def validate_args(args: argparse.Namespace) -> None:
    if args.allow_public_market_data is not True:
        raise PermissionError("explicit --allow-public-market-data required; no IO")
    if (
        type(args.interrupt_after_frames) is not int
        or type(args.max_frames) is not int
        or not 10 <= args.interrupt_after_frames < args.max_frames <= 2500
        or type(args.timeout_s) not in (float, int)
        or not 5 <= args.timeout_s <= 120
        or type(args.max_clock_offset_ms) is not int
        or not 100 <= args.max_clock_offset_ms <= 10000
        or type(args.max_clock_rtt_ms) is not int
        or not 50 <= args.max_clock_rtt_ms <= 5000
    ):
        raise ValueError("invalid bounded public reconnect settings")
    if not args.output_dir.is_absolute():
        raise ValueError("absolute output directory required")
    if args.output_dir.exists() and any(
        (args.output_dir / item).exists()
        for item in ("reconnect_original.sqlite", "phase1_reconnect_acceptance.json")
    ):
        raise FileExistsError("refusing to overwrite existing reconnect evidence")


async def run(args: argparse.Namespace) -> dict:
    validate_args(args)
    clock = await measure_public_clock(
        max_abs_offset_ms=args.max_clock_offset_ms,
        max_rtt_ms=args.max_clock_rtt_ms,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = OfflineSpotCaptureJournal(
        args.output_dir / "reconnect_original.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    adapter = OptInSpotIngressAdapter(source, symbol=args.symbol)
    fence = OfflineSpotWorkerFence(
        source, controller_id="spot_reconnect_acceptance",
        lease_ms=30000,
    )
    controller = OfflineSpotReconnectController(
        source, controller_id="spot_reconnect_acceptance",
        symbol=args.symbol, worker_fence=fence,
    )

    async def capture_once(session_id: str, sequence: int):
        if sequence not in (1, 2):
            raise ValueError("this acceptance only allows two distinct sessions")
        return await adapter.capture_public_once(
            allow_network=True,
            session_id=session_id,
            max_frames=args.max_frames,
            timeout_s=args.timeout_s,
            inject_disconnect_after_frames=(
                args.interrupt_after_frames if sequence == 1 else None
            ),
        )

    outcome = await controller.run_fenced_batch(
        capture_once, holder_id="manual_controlled_reconnect",
        max_attempts=2, backoff_s=1.0,
        heartbeat_interval_s=4.0,
    )
    attempts = controller.attempts()
    outages = controller.outages()
    result = {
        "scope": "PUBLIC_BINANCE_SPOT_READ_ONLY_CONTROLLED_RECONNECT",
        "clock_check": asdict(clock),
        "attempts": [asdict(x) for x in attempts],
        "outages": [asdict(x) for x in outages],
        "injected_disconnect_after_frames": args.interrupt_after_frames,
        "source_sessions_distinct": len({x.session_id for x in attempts}) == 2,
        "history_continuity_verified": False,
        "gap_imputed_or_backfilled": False,
        "orders_submitted": 0,
        "default_runtime_cutover": False,
        "recovered_book_update_id": outcome.latest_book_update_id,
        "result": outcome.status,
    }
    accepted = (
        outcome.status == "RECOVERED"
        and outcome.history_continuity_verified is False
        and len(attempts) == 2
        and result["source_sessions_distinct"]
        and attempts[0].status == "FAILED"
        and attempts[0].failure_code == "INJECTED_WS_DISCONNECT"
        and attempts[1].status == "SUCCESS"
        and len(outages) == 1
        and outages[0].restored_at_ms is not None
        and outages[0].coverage_verified is False
    )
    if accepted:
        first = attempts[0]
        second = attempts[1]
        first_originals = source.replay_as_of(
            first.session_id, as_of_ms=second.finished_at_ms,
        )
        second_book = OfflineSpotCaptureReplayer(source).replay(
            second.session_id, as_of_ms=second.finished_at_ms,
        )
        accepted = (
            len(first_originals) >= args.interrupt_after_frames
            and second_book.book is not None
            and second_book.depth_status == "SYNCED"
            and second_book.book.session_id == second.session_id
            and first.session_id != second.session_id
        )
        result["first_session_original_count"] = len(first_originals)
        result["second_session_record_count"] = second_book.total_records
        result["second_session_verified_depth"] = second_book.depth_status
    result["acceptance"] = (
        "PASSED_CONTROLLED_RECONNECT_WITH_UNRECOVERED_HISTORY_GAP"
        if accepted else "FAILED_CONTROLLED_RECONNECT"
    )
    (args.output_dir / "phase1_reconnect_acceptance.json").write_text(
        json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        summary = asyncio.run(run(args))
        print(json.dumps(summary, sort_keys=True))
        return 0 if summary["acceptance"].startswith("PASSED_") else 2
    except Exception as exc:
        # Static types only; never reveal private machine details or raw frames.
        print("PHASE1_RECONNECT_FAILED: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
