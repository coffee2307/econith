"""Isolated verified-only *historical* Spot runtime for the main ASGI app.

Provides one audited, opt-in closed-session research replay; no live socket,
background market feed, CCXT, brokerage, engine, order bridge or accounts.
It proves that actual MarketDataPipeline subscribers can only see raw frames
after original-byte capture, snapshot/diff sync, provenance manifest, and
authenticated separate-process durable receipt admission.

Important: this is NOT a continuous verified live-market source. As-of clocks
are original local receipt timestamps and output is explicitly HISTORICAL;
a replayed order book is never asserted to have existed before its actual
available_at_ms. Entirely separate from default legacy app boot.
"""
from __future__ import annotations

import asyncio
from argparse import Namespace
from dataclasses import asdict, dataclass
from pathlib import Path

from core.event_bus import Event, EventBus, RawMarketAdmissionDenied
from core.mode import QuantMode
from ci.phase1_offline_real_capture_chain import run as verify_chain
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.preprocessing.pipeline import MarketDataPipeline


class VerifiedResearchRuntimeError(RuntimeError):
    """Historical market event cannot enter the guarded subscriber graph."""


@dataclass(frozen=True)
class ResearchReplayStatus:
    mode: str
    market_data_policy: str
    session_id: str
    raw_count: int
    agg_trade_count: int
    final_depth_update_id: int
    producer_acks: int
    receiver_receipts: int
    emitted_raw_trades: int
    emitted_depth_books: int
    observed_tickers: int
    observed_volume_deltas: int
    observed_orderbook_imbalance: int
    original_bytes_read_only: bool
    local_receiver_authenticated: bool
    historical_replay_only: bool
    continuous_live_market: bool
    history_continuity_verified: bool
    execution_started: bool
    orders_submitted: int
    legacy_streamer_started: bool


class VerifiedSpotResearchRuntime:
    def __init__(self, *, bus: EventBus, status: ResearchReplayStatus) -> None:
        self.bus = bus
        self.status = status

    async def close(self) -> None:
        await self.bus.stop()


async def start_verified_research_runtime(
    *, original_db: Path, session_id: str, output_dir: Path,
) -> VerifiedSpotResearchRuntime:
    """Start a read-only subscriber graph only after durable source/ACK proof.

    A fresh output dir and closed real/fixture capture are required.
    No callbacks/routes with execution permissions are installed.
    """
    args = Namespace(
        source_db=original_db,
        output_dir=output_dir,
        session_id=session_id,
        authenticated_subprocess=True,
    )
    # Perform all expensive on-disk and IPC verification *before* allocating
    # EventBus publisher permissions or admitting a single market frame.
    report = await asyncio.to_thread(verify_chain, args)
    if (
        report["acceptance"] != "PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK"
        or report["book_status"] != "SYNCED"
        or report["original_trades_replayed"] <= 0
        or report["original_trades_replayed"] != report["producer_ack_count"]
        or report["producer_ack_count"] != report["independent_receiver_receipt_count"]
        or report["lineage_manifest_entries"] != report["producer_ack_count"]
        or report["cross_process_authenticated_transport"] is not True
        or report["network_requests"] != 0
        or report["orders_submitted"] != 0
    ):
        raise VerifiedResearchRuntimeError("source-to-subscriber research ACK gate failed")

    replay_source = OfflineSpotCaptureJournal(
        output_dir / "captured_copy.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    # This is the end-of-session AS-OF, NOT reconstructed historical order book
    # at each earlier trade's timestamp.
    with replay_source._connect() as conn:
        row = conn.execute("""
            SELECT closed_at_ms FROM offline_spot_capture_sessions
            WHERE session_id=?
        """, (session_id,)).fetchone()
    if row is None or row["closed_at_ms"] is None:
        raise VerifiedResearchRuntimeError("only a closed verified source may publish")
    replay = OfflineSpotCaptureReplayer(replay_source).replay(
        session_id, as_of_ms=row["closed_at_ms"],
        limit=5000,
    )
    if (
        replay.book is None or replay.depth_status != "SYNCED"
        or len(replay.trades) != report["original_trades_replayed"]
        or replay.total_records != report["original_records_replayed"]
    ):
        raise VerifiedResearchRuntimeError("original replay changed since durable ACK")

    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    publisher = bus.activate_verified_market_only(max_inflight=8)
    pipeline = MarketDataPipeline(bus)
    pipeline.register()
    stats = {"md.ticker":0, "indicator.volume_delta":0,"indicator.obi":0}
    all_observed = asyncio.Event()

    async def observed(event: Event) -> None:
        if event.payload.get("source") != "verified_closed_session_replay":
            raise VerifiedResearchRuntimeError("derived signal lacks approved replay provenance")
        stats[event.topic] += 1
        if (
            stats["md.ticker"] == len(replay.trades)
            and stats["indicator.volume_delta"] == len(replay.trades)
            and stats["indicator.obi"] == 1
        ):
            all_observed.set()

    for topic in stats:
        bus.subscribe(topic, observed)
    await bus.start()
    try:
        # No external consumer is registered. Only research projection
        # handlers observe this historical batch; execution is never started.
        for record in replay.trades:
            await publisher.publish_raw_confirmed(
                "md.aggTrade", frame=record.payload,
                source="verified_closed_session_replay",
                available_at_ms=record.received_at_ms,
                historical_replay=True,
                session_id=session_id,
            )
        book = replay.book
        # A synchronized full book is rendered into *legacy consumer schema*
        # from exact snapshot+diff evidence. Never emit raw depthUpdate as
        # though it were a depth20 REST partial snapshot.
        await publisher.publish_raw_confirmed(
            "md.depth",
            symbol=replay.symbol,
            frame={
                "lastUpdateId":book.last_update_id,
                "bids":[list(level) for level in book.bids[:20]],
                "asks":[list(level) for level in book.asks[:20]],
            },
            source="verified_closed_session_replay",
            available_at_ms=book.available_at_ms,
            historical_replay=True,
            session_id=session_id,
        )
        await asyncio.wait_for(all_observed.wait(), timeout=15.0)
        if (
            stats["md.ticker"] != len(replay.trades)
            or stats["indicator.volume_delta"] != len(replay.trades)
            or stats["indicator.obi"] != 1
        ):
            raise VerifiedResearchRuntimeError("derived consumer counts did not match verification")
        # Verify in-memory raw/derived handlers actually completed, not merely
        # that the queue contained the expected number of derived events.
        await bus.wait_verified_idle(timeout_s=15.0)
        # A direct legacy publisher is forbidden after activation, irrespective
        # of any claimed 'verified' source flag in the event body.
        try:
            await bus.publish(
                "md.aggTrade", frame=replay.trades[0].payload,
                source="verified_closed_session_replay",
            )
        except RawMarketAdmissionDenied:
            pass
        else:
            raise VerifiedResearchRuntimeError("legacy publisher could bypass guarded bus")
    except Exception:
        await bus.stop()
        raise
    return VerifiedSpotResearchRuntime(
        bus=bus,
        status=ResearchReplayStatus(
            mode="SIMULATION",
            market_data_policy="verified_only",
            session_id=session_id,
            raw_count=replay.total_records,
            agg_trade_count=len(replay.trades),
            final_depth_update_id=replay.book.last_update_id,
            producer_acks=report["producer_ack_count"],
            receiver_receipts=report["independent_receiver_receipt_count"],
            emitted_raw_trades=len(replay.trades),
            emitted_depth_books=1,
            observed_tickers=stats["md.ticker"],
            observed_volume_deltas=stats["indicator.volume_delta"],
            observed_orderbook_imbalance=stats["indicator.obi"],
            original_bytes_read_only=True,
            local_receiver_authenticated=True,
            historical_replay_only=True,
            continuous_live_market=False,
            history_continuity_verified=False,
            execution_started=False,
            orders_submitted=0,
            legacy_streamer_started=False,
        ),
    )
