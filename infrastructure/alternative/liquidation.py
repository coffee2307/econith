"""ECONITH :: infrastructure.alternative.liquidation

Forced-liquidation tracking (master plan, Phase 1, Step 2).

Aggregates recent liquidation events inside a rolling window. A spike in
liquidation notional often precedes volatility cascades, so this feeds both the
regime classifier and Sentinel risk context.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class LiquidationEvent:
    symbol: str
    side: str          # "BUY" (short liq) | "SELL" (long liq)
    price: float
    qty: float
    ts_ms: int

    @property
    def notional(self) -> float:
        return self.price * self.qty


@dataclass(slots=True, frozen=True)
class LiquidationSummary:
    symbol: str
    window_s: float
    count: int
    long_notional: float    # liquidated longs (SELL side)
    short_notional: float   # liquidated shorts (BUY side)
    total_notional: float


class LiquidationTracker:
    """Rolling-window aggregator of forced-liquidation notional."""

    def __init__(self, symbol: str = "BTCUSDT", window_s: float = 60.0) -> None:
        self._symbol = symbol.upper()
        self._window_s = window_s
        self._events: deque[LiquidationEvent] = deque()

    def add(self, event: LiquidationEvent, *, now: float | None = None) -> None:
        self._events.append(event)
        self._evict(now if now is not None else time.time())

    def _evict(self, now: float) -> None:
        cutoff_ms = (now - self._window_s) * 1000
        while self._events and self._events[0].ts_ms < cutoff_ms:
            self._events.popleft()

    def summary(self, *, now: float | None = None) -> LiquidationSummary:
        self._evict(now if now is not None else time.time())
        longs = sum(e.notional for e in self._events if e.side == "SELL")
        shorts = sum(e.notional for e in self._events if e.side == "BUY")
        return LiquidationSummary(
            symbol=self._symbol,
            window_s=self._window_s,
            count=len(self._events),
            long_notional=longs,
            short_notional=shorts,
            total_notional=longs + shorts,
        )
