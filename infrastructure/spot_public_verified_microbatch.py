"""Explicit, finite, public-Spot VERIFIED RESEARCH microbatch data plane.

It reads bounded real TLS WSS/REST sessions, durably captures original bytes,
replays a fully synchronized Spot book, then requires verified lineage and
HMAC-authenticated local subprocess receiver-owned ACKs BEFORE a guarded
SIMULATION EventBus can publish to real MarketDataPipeline subscribers.

It is NOT tick-by-tick live trading or a gap-free continuous market feed:
each session is sealed and verified before any research publication. Between
sessions original market observations are missing by construction; every
report declares history_continuity_verified=False. No CCXT, order bridge,
production hooks, remote brokers, API keys, user accounts or real trades.

This module is not imported by main.py or any background startup.
"""
from __future__ import annotations

import asyncio
import json
import time
from argparse import Namespace
from pathlib import Path
from typing import Any, Awaitable, Callable

from ci.phase1_offline_real_capture_chain import run as verify_chain
from core.event_bus import Event, EventBus, RawMarketAdmissionDenied
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import CrossProcessDeliveryError
from infrastructure.preprocessing.pipeline import MarketDataPipeline
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportResult,
)
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController
from infrastructure.spot_worker_fence import OfflineSpotWorkerFence


class VerifiedPublicResearchError(RuntimeError):
    """A public market session failed authenticated research admission."""


CaptureCallable = Callable[[str, int], Awaitable[SpotTransportResult]]


async def observe_verified_public_batches(
    *, output_dir: Path,
    allow_network: bool = False,
    symbol: str = "BTCUSDT",
    cycles: int = 2,
    max_frames: int = 160,
    timeout_s: float = 45,
    max_attempts: int = 2,
    capture_once: CaptureCallable | None = None,
) -> dict[str, Any]:
    """Finite research microbatches with strict source → consumer ordering.

    Production calls must explicitly opt into *public* data. An injected
    capture_once is for CI/fixture transport only and does not prove TLS.
    On a failed/incomplete capture, *no unverified market event* is emitted.
    A persisted per-session audit is written before any next public session.
    """
    if allow_network is not True:
        raise PermissionError("explicit public market-data research opt-in required")
    if symbol != "BTCUSDT":
        raise ValueError("only reviewed BTCUSDT research source supported")
    if (
        type(cycles) is not int or not 1 <= cycles <= 4
        or type(max_frames) is not int or not 10 <= max_frames <= 2000
        or type(max_attempts) is not int or not 1 <= max_attempts <= 3
        or type(timeout_s) not in (float, int) or not 5 <= timeout_s <= 120
    ):
        raise ValueError("invalid finite microbatch source/resource limits")
    root = output_dir.resolve()
    if not output_dir.is_absolute() or root.exists():
        raise FileExistsError("new absolute empty root is required; no overwrite or resume")
    root.mkdir(parents=True, exist_ok=False)
    mode = lambda: QuantMode.SIMULATION
    journal = OfflineSpotCaptureJournal(root / "original_public.sqlite", mode_provider=mode)
    adapter = OptInSpotIngressAdapter(journal, symbol=symbol, queue_capacity=128)
    fence = OfflineSpotWorkerFence(
        journal, controller_id="public_verified_microbatch", lease_ms=30_000,
    )
    controller = OfflineSpotReconnectController(
        journal, controller_id="public_verified_microbatch", symbol=symbol,
        worker_fence=fence,
    )
    bus = EventBus(mode_provider=mode)
    publisher = bus.activate_verified_market_only()
    pipeline = MarketDataPipeline(bus)
    pipeline.register()
    stats = {"md.ticker": 0, "indicator.volume_delta": 0, "indicator.obi": 0}
    pending = asyncio.Event()

    async def observe(event: Event) -> None:
        if (
            event.payload.get("source") != "verified_sealed_public_microbatch"
        ):
            raise VerifiedPublicResearchError("derived consumer got unexpected source")
        stats[event.topic] += 1
        pending.set()

    for topic in stats:
        bus.subscribe(topic, observe)
    sessions: list[dict[str, Any]] = []
    await bus.start()
    try:
        for cycle in range(cycles):
            async def _capture(session_id: str, sequence: int) -> SpotTransportResult:
                if capture_once is not None:
                    return await capture_once(session_id, sequence)
                return await adapter.capture_public_once(
                    allow_network=True, session_id=session_id,
                    max_frames=max_frames, timeout_s=timeout_s,
                )

            result = await controller.run_fenced_batch(
                _capture, holder_id="microbatch_public_reader",
                max_attempts=max_attempts, backoff_s=1.0,
                heartbeat_interval_s=4.0,
            )
            if result.status != "RECOVERED" or not result.last_session_id:
                raise VerifiedPublicResearchError(
                    "no valid public captured/synchronized session; stop without research emit"
                )
            sid = result.last_session_id
            verified_dir = root / f"cycle_{cycle + 1:02d}"
            # The verified_chain reads ORIGINAL SQLite via read-only backup and
            # enforces a separate market DB and independent HMAC receiver DB.
            proof = await asyncio.to_thread(
                verify_chain,
                Namespace(
                    source_db=journal.path, output_dir=verified_dir,
                    session_id=sid, authenticated_subprocess=True,
                ),
            )
            if (
                proof["acceptance"] != "PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK"
                or proof["book_status"] != "SYNCED"
                or proof["producer_ack_count"] <= 0
                or proof["producer_ack_count"] != proof["receiver_receipts"]
                if "receiver_receipts" in proof else False
            ):
                raise VerifiedPublicResearchError("durable verified research chain refused")
            if (
                proof["producer_ack_count"]
                != proof["independent_receiver_receipt_count"]
                or proof["original_trades_replayed"] != proof["producer_ack_count"]
                or proof["lineage_manifest_entries"] != proof["producer_ack_count"]
                or proof["cross_process_authenticated_transport"] is not True
                or proof["network_requests"] != 0
                or proof["orders_submitted"] != 0
            ):
                raise VerifiedPublicResearchError("receiver/source lineage or ACK mismatch")

            with journal._connect() as conn:
                row = conn.execute(
                    "SELECT closed_at_ms FROM offline_spot_capture_sessions WHERE session_id=?",
                    (sid,),
                ).fetchone()
            if row is None or row["closed_at_ms"] is None:
                raise VerifiedPublicResearchError("unclosed public source cannot be emitted")
            replay = OfflineSpotCaptureReplayer(journal).replay(
                sid, as_of_ms=row["closed_at_ms"], limit=5000,
            )
            if (
                replay.book is None or replay.depth_status != "SYNCED"
                or len(replay.trades) != proof["original_trades_replayed"]
                or replay.total_records != proof["original_records_replayed"]
            ):
                raise VerifiedPublicResearchError("original source changed since receiver ACK")
            observed_before = stats.copy()
            for trade in replay.trades:
                await publisher.publish_raw(
                    "md.aggTrade",
                    frame=trade.payload, source="verified_sealed_public_microbatch",
                    available_at_ms=trade.received_at_ms,
                    observed_market_time_ms=trade.payload["E"],
                    session_id=sid, historical_replay=True,
                    bounded_public_session=True,
                )
            book = replay.book
            await publisher.publish_raw(
                "md.depth",
                symbol=replay.symbol,
                frame={
                    "lastUpdateId": book.last_update_id,
                    "bids": [list(level) for level in book.bids[:20]],
                    "asks": [list(level) for level in book.asks[:20]],
                },
                source="verified_sealed_public_microbatch",
                available_at_ms=book.available_at_ms,
                session_id=sid, historical_replay=True,
                bounded_public_session=True,
            )
            wanted = {
                "md.ticker": len(replay.trades),
                "indicator.volume_delta": len(replay.trades),
                "indicator.obi": 1,
            }
            deadline = asyncio.get_running_loop().time() + 30
            while True:
                emitted = {
                    key: stats[key] - observed_before[key] for key in stats
                }
                if emitted == wanted:
                    break
                if any(emitted[k] > wanted[k] for k in wanted):
                    raise VerifiedPublicResearchError("duplicate/extra derived events")
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise VerifiedPublicResearchError("research subscribers did not acknowledge data")
                pending.clear()
                try:
                    await asyncio.wait_for(pending.wait(), timeout=remaining)
                except TimeoutError as exc:
                    raise VerifiedPublicResearchError(
                        "research subscribers did not acknowledge data"
                    ) from exc
            # Also refuse legacy publication even with a forged source label.
            try:
                await bus.publish(
                    "md.aggTrade", frame=replay.trades[0].payload,
                    source="verified_sealed_public_microbatch",
                )
            except RawMarketAdmissionDenied:
                pass
            else:
                raise VerifiedPublicResearchError("legacy market publisher bypassed protected bus")
            sessions.append({
                "cycle": cycle + 1,
                "session_id": sid,
                "originals": replay.total_records,
                "verified_trades": len(replay.trades),
                "verified_book_update_id": book.last_update_id,
                "producer_acks": proof["producer_ack_count"],
                "receiver_receipts": proof["independent_receiver_receipt_count"],
                "derived": emitted,
                "historical_replay_only": True,
                "history_continuity_verified": False,
                "unobserved_between_sessions": cycle > 0,
                "legacy_streamer_started": False,
            })
            # An audit record must be retained independently of UI state.
            (root / "microbatch_progress.json").write_text(
                json.dumps({
                    "status":"RESEARCH_ONLY",
                    "completed_cycles":len(sessions),
                    "sessions":sessions,
                    "history_continuity_verified":False,
                    "orders_submitted":0,
                },sort_keys=True,indent=2)+"\n",
                encoding="utf-8",
            )
        return {
            "acceptance": "PASSED_BOUNDED_PUBLIC_VERIFIED_MICROBATCH_RESEARCH",
            "completed_cycles":len(sessions),
            "sessions":sessions,
            "source_sessions_distinct":len({s["session_id"] for s in sessions}) == cycles,
            "historical_replay_only":True,
            "continuous_live_market":False,
            "history_continuity_verified":False,
            "outage_and_gap_free_stream_proven":False,
            "local_receiver_authenticated":True,
            "legacy_streamer_started":False,
            "execution_started":False,
            "orders_submitted":0,
            "receiver_scope":"HMAC_AUTHENTICATED_LOCAL_SUBPROCESS_PIPE_ONLY",
            "public_tls_peer_confirmed":capture_once is None,
        }
    finally:
        await bus.stop()
