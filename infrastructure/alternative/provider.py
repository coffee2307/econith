"""ECONITH :: infrastructure.alternative.provider

Coordinates the alternative-data trackers and publishes synchronised samples
onto the EventBus (master plan, Phase 1, Step 2).

Mock-first: when no Binance credentials are present it synthesises plausible
funding-rate / open-interest / liquidation series. The published topics are the
contract every downstream consumer relies on:

    alt.funding_rate   {funding_rate, time_to_funding_s, forward_filled, ...}
    alt.open_interest  {open_interest, oi_change, oi_change_pct, ...}
    alt.liquidation    {count, long_notional, short_notional, total_notional}
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

from config.environment import get_environment
from core.event_bus import Event, EventBus
from infrastructure.alternative.funding_rate import FundingRateTracker
from infrastructure.alternative.liquidation import (
    LiquidationEvent,
    LiquidationTracker,
)
from infrastructure.alternative.open_interest import (
    OpenInterestSample,
    OpenInterestTracker,
)

logger = logging.getLogger("econith.infra.alternative")


class AlternativeDataProvider:
    """Periodic publisher of derivatives / alternative data."""

    def __init__(
        self,
        bus: EventBus,
        symbol: str = "BTCUSDT",
        interval_s: float = 2.0,
        force_mock: bool | None = None,
    ) -> None:
        self._env = get_environment()
        self._bus = bus
        self._symbol = symbol.upper()
        self._interval = interval_s
        self._funding = FundingRateTracker(symbol)
        self._oi = OpenInterestTracker(symbol)
        self._liq = LiquidationTracker(symbol)
        self._last_price: float = 60_000.0
        self._oi_level: float = 1.0e9  # seed notional Open Interest
        self._last_oi: OpenInterestSample | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

        if force_mock is None:
            self._mock = not self._env.has_binance_data_credentials
        else:
            self._mock = force_mock

    # -- lifecycle ------------------------------------------------------------
    def register(self) -> None:
        # Track the live price so mock liquidations are priced realistically.
        self._bus.subscribe("md.ticker", self._on_ticker)
        # A flash crash should spawn a burst of long liquidations.
        self._bus.subscribe("sentinel.emergency", self._on_emergency)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self.run(), name="alt-data")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- handlers -------------------------------------------------------------
    async def _on_ticker(self, event: Event) -> None:
        self._last_price = float(event.payload.get("price", self._last_price))

    async def _on_emergency(self, event: Event) -> None:
        if event.payload.get("action") != "FREEZE":
            return
        # synthesise a cluster of long liquidations on a downside shock
        now_ms = int(time.time() * 1000)
        for _ in range(random.randint(3, 8)):
            self._liq.add(
                LiquidationEvent(
                    symbol=self._symbol,
                    side="SELL",
                    price=self._last_price,
                    qty=round(abs(random.gauss(2.0, 1.0)) + 0.1, 3),
                    ts_ms=now_ms,
                )
            )

    # -- main loop ------------------------------------------------------------
    async def run(self) -> None:
        while self._running:
            if self._mock:
                self._tick_mock()
            await self._publish()
            await asyncio.sleep(self._interval)

    def _tick_mock(self) -> None:
        # funding rate: small mean-reverting series around 0.01%
        new_rate = self._funding.sample().funding_rate * 0.9 + random.gauss(0.0001, 0.00005) * 0.1
        self._funding.update(new_rate)
        # open interest: slow random walk (single authoritative update per tick)
        self._oi_level = max(1.0e6, self._oi_level * (1.0 + random.gauss(0.0, 0.004)))
        self._last_oi = self._oi.update(self._oi_level)

    async def _publish(self) -> None:
        fr = self._funding.sample()
        await self._bus.publish(
            "alt.funding_rate",
            symbol=fr.symbol,
            funding_rate=round(fr.funding_rate, 8),
            time_to_funding_s=round(fr.time_to_funding_s, 1),
            forward_filled=fr.forward_filled,
        )
        if self._last_oi is not None:
            oi = self._last_oi
            await self._bus.publish(
                "alt.open_interest",
                symbol=oi.symbol,
                open_interest=round(oi.open_interest, 2),
                oi_change=round(oi.oi_change, 2),
                oi_change_pct=round(oi.oi_change_pct, 6),
            )
        liq = self._liq.summary()
        await self._bus.publish(
            "alt.liquidation",
            symbol=liq.symbol,
            count=liq.count,
            long_notional=round(liq.long_notional, 2),
            short_notional=round(liq.short_notional, 2),
            total_notional=round(liq.total_notional, 2),
        )
