"""ECONITH :: infrastructure.indicators.orderflow.obi

Order-flow microstructure indicators (master plan, Phase 1, Step 3):

  * **Orderbook Imbalance (OBI)** over the top-20 levels:
        OBI = (Vbid - Vask) / (Vbid + Vask)   in [-1, +1]
    where Vbid/Vask are the summed quantities of the best 20 bid/ask levels.
    Positive => buy-side pressure, negative => sell-side pressure.

  * **Volume Delta** -- the net of aggressive market BUY vs market SELL volume
    inside a rolling time window. Combined with OBI this resists spoofing
    (resting quotes that are placed then cancelled).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from infrastructure.preprocessing.cleaner import AggTrade, DepthSnapshot


@dataclass(slots=True, frozen=True)
class OBIResult:
    obi: float
    bid_volume: float
    ask_volume: float
    levels: int


def compute_obi(snapshot: DepthSnapshot, levels: int = 20) -> OBIResult:
    """Compute Orderbook Imbalance over the top-N levels of a snapshot."""
    bid_vol = sum(qty for _, qty in snapshot.bids[:levels])
    ask_vol = sum(qty for _, qty in snapshot.asks[:levels])
    total = bid_vol + ask_vol
    obi = (bid_vol - ask_vol) / total if total > 0 else 0.0
    used = min(levels, len(snapshot.bids), len(snapshot.asks))
    return OBIResult(obi=obi, bid_volume=bid_vol, ask_volume=ask_vol, levels=used)


@dataclass(slots=True, frozen=True)
class VolumeDeltaResult:
    volume_delta: float
    buy_volume: float
    sell_volume: float
    window_s: float
    trade_count: int


class VolumeDeltaTracker:
    """Rolling-window accumulator of signed aggressive trade volume."""

    def __init__(self, window_s: float = 10.0) -> None:
        self._window_s = window_s
        # store (monotonic_ts, signed_qty, abs_qty, is_buy)
        self._events: deque[tuple[float, float, float, bool]] = deque()

    def add(self, trade: AggTrade, *, now: float | None = None) -> None:
        ts = time.monotonic() if now is None else now
        self._events.append((ts, trade.signed_qty, trade.qty, trade.is_aggressive_buy))
        self._evict(ts)

    def _evict(self, now: float) -> None:
        cutoff = now - self._window_s
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()

    def value(self, *, now: float | None = None) -> VolumeDeltaResult:
        ts = time.monotonic() if now is None else now
        self._evict(ts)
        buy = sum(abs_q for _, _, abs_q, is_buy in self._events if is_buy)
        sell = sum(abs_q for _, _, abs_q, is_buy in self._events if not is_buy)
        return VolumeDeltaResult(
            volume_delta=buy - sell,
            buy_volume=buy,
            sell_volume=sell,
            window_s=self._window_s,
            trade_count=len(self._events),
        )
