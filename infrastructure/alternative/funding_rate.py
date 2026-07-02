"""ECONITH :: infrastructure.alternative.funding_rate

Funding-rate tracking with time-alignment (master plan, Phase 1, Step 2).

Perpetual funding settles on a fixed cadence (Binance: every 8h). Between
settlements the rate is *forward-filled* (the last known value is carried
forward for each subsequent second) so the feature stream is gap-free. A
``time_to_funding`` countdown is exposed so the AI can perceive expiry pressure
on open positions.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

FUNDING_INTERVAL_S = 8 * 3600  # Binance perpetual funding cadence (8 hours)


@dataclass(slots=True, frozen=True)
class FundingSample:
    symbol: str
    funding_rate: float        # e.g. 0.0001 == 0.01%
    next_funding_ms: int
    time_to_funding_s: float   # countdown feature
    forward_filled: bool


class FundingRateTracker:
    """Holds the latest funding rate and forward-fills between settlements."""

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self._symbol = symbol.upper()
        self._rate: float = 0.0
        self._next_funding_ms: int = int(time.time() * 1000) + FUNDING_INTERVAL_S * 1000
        self._has_real = False

    def update(self, funding_rate: float, next_funding_ms: int | None = None) -> None:
        """Ingest a freshly observed funding rate (live or mock)."""
        self._rate = float(funding_rate)
        if next_funding_ms is not None:
            self._next_funding_ms = int(next_funding_ms)
        self._has_real = True

    def sample(self, *, now_ms: int | None = None) -> FundingSample:
        """Return the current (possibly forward-filled) funding sample."""
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        # roll the settlement window forward if it has elapsed
        while now >= self._next_funding_ms:
            self._next_funding_ms += FUNDING_INTERVAL_S * 1000
        ttf = max(0.0, (self._next_funding_ms - now) / 1000.0)
        return FundingSample(
            symbol=self._symbol,
            funding_rate=self._rate,
            next_funding_ms=self._next_funding_ms,
            time_to_funding_s=ttf,
            forward_filled=not self._has_real,
        )
