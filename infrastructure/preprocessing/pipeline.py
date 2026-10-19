"""ECONITH :: infrastructure.preprocessing.pipeline

The order-flow processing pipeline. It subscribes to raw market-data frames on
the central EventBus, cleans them (cleaner), computes microstructure indicators
(OBI + Volume Delta), and republishes derived signals back onto the bus:

    md.aggTrade  ->  md.ticker            (last trade price)
                 ->  indicator.volume_delta
    md.depth     ->  indicator.obi

Everything downstream (Sentinel, the metrics WebSocket, future AI agents) reads
these derived topics rather than parsing raw frames.
"""
from __future__ import annotations

import logging

from core.event_bus import Event, EventBus, VerifiedDerivedPublisher
from infrastructure.indicators.orderflow.obi import (
    VolumeDeltaTracker,
    compute_obi,
)
from infrastructure.preprocessing.cleaner import clean_agg_trade, clean_depth

logger = logging.getLogger("econith.infra.preprocessing.pipeline")


class MarketDataPipeline:
    """Glues cleaner + indicators to the EventBus."""

    def __init__(
        self,
        bus: EventBus,
        obi_levels: int = 20,
        volume_window_s: float = 10.0,
        verified_derived_publisher: VerifiedDerivedPublisher | None = None,
    ) -> None:
        self._bus = bus
        self._verified_derived_publisher = verified_derived_publisher
        self._obi_levels = obi_levels
        self._volume_window_s = volume_window_s
        self._vd = VolumeDeltaTracker(window_s=volume_window_s)
        self._last_price: float | None = None

    async def _publish_derived(self, topic: str, **payload) -> None:
        publisher = self._verified_derived_publisher
        if publisher is None:
            await self._bus.publish(topic, **payload)
        else:
            await publisher.publish(topic, **payload)

    def register(self) -> None:
        """Wire pipeline handlers onto the bus (call once at startup)."""
        self._bus.subscribe("md.aggTrade", self._on_agg_trade)
        self._bus.subscribe("md.depth", self._on_depth)
        logger.info("orderflow pipeline registered")

    def reset_verified_research_session(self) -> None:
        """Never carry rolling volume/price state across unknown WSS gaps.

        Called ONLY between sealed, separately verified research sessions,
        once the preceding EventBus graph has fully drained.
        """
        self._vd = VolumeDeltaTracker(window_s=self._volume_window_s)
        self._last_price = None

    @staticmethod
    def _verified_research_metadata(event: Event) -> dict:
        """Optional source coordinates for only the guarded research graph.

        Ordinary legacy streamer frames contain none of these keys and
        retain their original derived-payload contract. Missing/partial
        verified metadata is a handler failure, not silent loss of lineage.
        """
        names = (
            "verified_session_id", "verified_source_record_id",
            "verified_source_ordinal", "verified_source_available_at_ms",
        )
        present = [name in event.payload for name in names]
        if not any(present):
            return {}
        if not all(present):
            raise ValueError("partial verified research source lineage")
        return {name: event.payload[name] for name in names}

    async def _on_agg_trade(self, event: Event) -> None:
        trade = clean_agg_trade(event.payload["frame"])
        if trade is None:
            return

        research_metadata = self._verified_research_metadata(event)
        # Replaying a sealed research session must depend on the original
        # local source-availability clock, NEVER on how fast this process
        # happens to replay the historical batch. Legacy realtime frames
        # retain the existing monotonic clock implementation unchanged.
        research_clock = (
            research_metadata["verified_source_available_at_ms"] / 1000.0
            if research_metadata else None
        )
        self._last_price = trade.price
        self._vd.add(trade, now=research_clock)
        vd = self._vd.value(now=research_clock)

        await self._publish_derived(
            "md.ticker",
            symbol=trade.symbol,
            price=trade.price,
            event_ms=trade.event_ms,
            source=event.payload.get("source", "unknown"),
            **self._verified_research_metadata(event),
        )
        await self._publish_derived(
            "indicator.volume_delta",
            symbol=trade.symbol,
            volume_delta=vd.volume_delta,
            buy_volume=vd.buy_volume,
            sell_volume=vd.sell_volume,
            window_s=vd.window_s,
            trade_count=vd.trade_count,
            source=event.payload.get("source", "unknown"),
            **self._verified_research_metadata(event),
        )

    async def _on_depth(self, event: Event) -> None:
        symbol = event.payload["symbol"]
        snapshot = clean_depth(event.payload["frame"], symbol, levels=self._obi_levels)
        if snapshot is None:
            return

        result = compute_obi(snapshot, levels=self._obi_levels)
        await self._publish_derived(
            "indicator.obi",
            symbol=symbol,
            obi=result.obi,
            bid_volume=result.bid_volume,
            ask_volume=result.ask_volume,
            levels=result.levels,
            mid=snapshot.mid,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            source=event.payload.get("source", "unknown"),
            **self._verified_research_metadata(event),
        )
