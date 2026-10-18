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
from typing import Mapping

from core.event_bus import Event, EventBus, RawMarketAdmissionDenied
from core.mode import QuantMode
from ci.phase1_offline_real_capture_chain import run as verify_chain
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.preprocessing.pipeline import MarketDataPipeline
from infrastructure.market_derived_research_receipts import (
    OfflineDerivedResearchReceiptLedger,
)
from infrastructure.market_named_research_subscribers import (
    OfflineNamedResearchSubscriber, checked_named_recovery_paths,
)

_NAMED_RESEARCH_CONSUMERS = ("research_audit", "research_monitor")


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
    durable_derived_research_receipts: int
    replayed_derived_research_receipts: int
    durable_named_research_receipts: int
    replayed_named_research_receipts: int
    named_research_subscribers: int
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
    derived_receipt_db: Path | None = None,
    named_receipt_dbs: Mapping[str, Path] | None = None,
) -> VerifiedSpotResearchRuntime:
    """Start a read-only subscriber graph only after durable source/ACK proof.

    A fresh output dir and closed real/fixture capture are required.
    When derived_receipt_db is supplied it MUST be an existing observer
    ledger from an earlier aborted/completed run, independent of fresh
    scratch proof DBs. This is an explicit research recovery option; it
    does NOT enable any external EventBus callback or live trade.
    """
    if derived_receipt_db is not None:
        if (
            not isinstance(derived_receipt_db, Path)
            or not derived_receipt_db.is_absolute()
            or not derived_receipt_db.is_file()
            or derived_receipt_db.resolve() == original_db.resolve()
            or derived_receipt_db.resolve().is_relative_to(output_dir.resolve())
        ):
            raise VerifiedResearchRuntimeError(
                "recovery requires an existing independent derived observer ledger"
            )
    named_paths = checked_named_recovery_paths(
        output_dir=output_dir, original_db=original_db,
        primary_receipts=(
            derived_receipt_db or output_dir / "derived_consumer_receipts.sqlite"
        ),
        names=_NAMED_RESEARCH_CONSUMERS, recovery=named_receipt_dbs,
    )
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
    derived_publisher = bus.activate_verified_derived_only()
    pipeline = MarketDataPipeline(bus, verified_derived_publisher=derived_publisher)
    pipeline.register()
    stats = {"md.ticker":0, "indicator.volume_delta":0,"indicator.obi":0}
    derived_ledger = OfflineDerivedResearchReceiptLedger(
        derived_receipt_db or output_dir / "derived_consumer_receipts.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    named_subscribers = [
        OfflineNamedResearchSubscriber(
            named_paths[name], name=name,
            expected_source="verified_closed_session_replay",
            mode_provider=lambda: QuantMode.SIMULATION,
        ) for name in _NAMED_RESEARCH_CONSUMERS
    ]
    for consumer in named_subscribers:
        consumer.register(bus)
    replayed_receipts = {"count": 0}
    all_observed = asyncio.Event()

    async def observed(event: Event) -> None:
        if event.payload.get("source") != "verified_closed_session_replay":
            raise VerifiedResearchRuntimeError("derived signal lacks approved replay provenance")
        acknowledgement = derived_ledger.accept(
            topic=event.topic,
            session_id=event.payload["verified_session_id"],
            ordinal=event.payload["verified_source_ordinal"],
            source_record_id=event.payload["verified_source_record_id"],
            source_available_at_ms=event.payload["verified_source_available_at_ms"],
            projection=event.payload,
        )
        if acknowledgement.replayed:
            replayed_receipts["count"] += 1
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
        for ordinal, record in enumerate(replay.trades, start=1):
            await publisher.publish_raw_confirmed(
                "md.aggTrade", frame=record.payload,
                source="verified_closed_session_replay",
                available_at_ms=record.received_at_ms,
                historical_replay=True,
                session_id=session_id,
                verified_session_id=session_id,
                verified_source_record_id=f"{session_id}:aggTrade:{record.capture_id}",
                verified_source_ordinal=ordinal,
                verified_source_available_at_ms=record.received_at_ms,
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
            verified_session_id=session_id,
            verified_source_record_id=f"{session_id}:book:{book.last_update_id}",
            verified_source_ordinal=1,
            verified_source_available_at_ms=book.available_at_ms,
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
        wanted_receipts = {
            "md.ticker":len(replay.trades),
            "indicator.volume_delta":len(replay.trades),
            "indicator.obi":1,
        }
        durable_receipts = {
            topic:derived_ledger.verified_count(session_id, topic)
            for topic in wanted_receipts
        }
        if durable_receipts != wanted_receipts:
            raise VerifiedResearchRuntimeError(
                "derived consumer observation lacks durable source-bound ACK"
            )
        named_proofs = [
            subscriber.verify(session_id, wanted_receipts)
            for subscriber in named_subscribers
        ]
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
            durable_derived_research_receipts=sum(durable_receipts.values()),
            replayed_derived_research_receipts=replayed_receipts["count"],
            durable_named_research_receipts=sum(x.durable_receipts for x in named_proofs),
            replayed_named_research_receipts=sum(x.replayed_receipts for x in named_proofs),
            named_research_subscribers=len(named_proofs),
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
