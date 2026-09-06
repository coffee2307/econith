"""ECONITH :: infrastructure.alternative.open_interest

Open-Interest tracking (master plan, Phase 1, Step 2).

Open Interest = total notional of outstanding derivative contracts. Its rate of
change is a regime-classification feature (feeds the GMM/HMM switcher later),
so we expose both the level and a smoothed delta.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class OpenInterestSample:
    symbol: str
    open_interest: float
    oi_change: float        # absolute change vs previous sample
    oi_change_pct: float    # fractional change vs previous sample


class OpenInterestTracker:
    """Tracks the latest Open Interest level and its change rate."""

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self._symbol = symbol.upper()
        self._oi: float | None = None
        self._prev: float | None = None

    def update(self, open_interest: float) -> OpenInterestSample:
        oi = float(open_interest)
        self._prev = self._oi
        self._oi = oi
        change = 0.0 if self._prev is None else oi - self._prev
        change_pct = (
            0.0 if not self._prev else change / self._prev
        )
        return OpenInterestSample(
            symbol=self._symbol,
            open_interest=oi,
            oi_change=change,
            oi_change_pct=change_pct,
        )

    @property
    def level(self) -> float | None:
        return self._oi
