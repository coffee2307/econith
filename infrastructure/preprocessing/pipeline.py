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

from core.event_bus import Event, EventBus
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
    ) -> None:
        self._bus = bus
        self._obi_levels = obi_levels
        self._vd = VolumeDeltaTracker(window_s=volume_window_s)
        self._last_price: float | None = None

    def register(self) -> None:
        """Wire pipeline handlers onto the bus (call once at startup)."""
        self._bus.subscribe("md.aggTrade", self._on_agg_trade)
        self._bus.subscribe("md.depth", self._on_depth)
        logger.info("orderflow pipeline registered")

    async def _on_agg_trade(self, event: Event) -> None:
        trade = clean_agg_trade(event.payload["frame"])
        if trade is None:
            return

        self._last_price = trade.price
        self._vd.add(trade)
        vd = self._vd.value()

        await self._bus.publish(
            "md.ticker",
            symbol=trade.symbol,
            price=trade.price,
            event_ms=trade.event_ms,
        )
        await self._bus.publish(
            "indicator.volume_delta",
            symbol=trade.symbol,
            volume_delta=vd.volume_delta,
            buy_volume=vd.buy_volume,
            sell_volume=vd.sell_volume,
            window_s=vd.window_s,
            trade_count=vd.trade_count,
        )

    async def _on_depth(self, event: Event) -> None:
        symbol = event.payload["symbol"]
        snapshot = clean_depth(event.payload["frame"], symbol, levels=self._obi_levels)
        if snapshot is None:
            return

        result = compute_obi(snapshot, levels=self._obi_levels)
        await self._bus.publish(
            "indicator.obi",
            symbol=symbol,
            obi=result.obi,
            bid_volume=result.bid_volume,
            ask_volume=result.ask_volume,
            levels=result.levels,
            mid=snapshot.mid,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
        )
