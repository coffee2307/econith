"""ECONITH :: ai.simulator_engine.market_context

Live *quant-side* read-model consumed by the World Simulation Kernel.

This is the **Quant -> World** ingestion buffer. The Unified Simulation Kernel
subscribes to the microstructure / AI / risk topics on the EventBus and feeds
them here; the World agents then read a single, decoupled snapshot of "what the
markets are doing right now" without ever touching the Quant layer directly.

Design notes
------------
* Pure data + cheap derived statistics -- **no** orchestration, **no** bus
  access. The kernel owns all I/O; this object only aggregates.
* Rolling windows are bounded ``deque``s so the hot path stays O(window).
* ``volatility`` and ``stress`` are normalised into ``[0, 1]`` via saturating
  transforms so downstream feedback math is scale-stable regardless of the raw
  units emitted by the (mock or live) feeds.
"""
from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field

# Regime labels shared with the Quant regime classifier.
from ai.regime.classifier import REGIMES

__all__ = ["MarketContext", "MarketSnapshot"]


def _stdev(seq: deque[float]) -> float:
    if len(seq) < 2:
        return 0.0
    try:
        return statistics.pstdev(seq)
    except statistics.StatisticsError:
        return 0.0


def _mean(seq: deque[float]) -> float:
    return sum(seq) / len(seq) if seq else 0.0


@dataclass(slots=True, frozen=True)
class MarketSnapshot:
    """Immutable derived view handed to the World agents each tick."""

    regime: str
    regime_confidence: float
    ai_direction: float
    ai_confidence: float
    ai_action: str
    volatility: float          # normalised realised vol   [0, 1]
    sell_pressure: float       # normalised net selling     [0, 1]
    liquidation: float         # normalised cascade size    [0, 1]
    stress: float              # composite market stress    [0, 1]
    funding_rate: float
    oi_change_pct: float
    sentinel_mode: str

    def is_crisis(self) -> bool:
        """A crisis print the Corporate AIs react to (high-vol + selling)."""
        return self.stress >= 0.55 or (
            self.regime == "VOLATILE" and self.sell_pressure >= 0.4
        )


@dataclass(slots=True)
class MarketContext:
    """Mutable aggregator updated by the kernel from EventBus frames."""

    window: int = 90
    liq_reference: float = 5.0e6      # USD notional that saturates the cascade term
    vd_reference: float = 250.0       # |volume_delta| that saturates the vol term

    regime: str = "CALM"
    regime_confidence: float = 0.0
    ai_direction: float = 0.0
    ai_confidence: float = 0.0
    ai_action: str = "FLAT"
    funding_rate: float = 0.0
    oi_change_pct: float = 0.0
    liquidation_notional: float = 0.0
    sentinel_mode: str = "NORMAL"

    _obi: deque[float] = field(default_factory=lambda: deque(maxlen=90))
    _vd: deque[float] = field(default_factory=lambda: deque(maxlen=90))
    _liq: deque[float] = field(default_factory=lambda: deque(maxlen=90))

    def __post_init__(self) -> None:
        # Resize the deques to the configured window (dataclass default is fixed).
        self._obi = deque(self._obi, maxlen=self.window)
        self._vd = deque(self._vd, maxlen=self.window)
        self._liq = deque(self._liq, maxlen=self.window)

    # -- ingestion (called by the kernel's bus handlers) ----------------------
    def ingest_ai_signal(
        self, direction: float, confidence: float, action: str,
        regime: str, regime_confidence: float,
    ) -> None:
        self.ai_direction = max(-1.0, min(1.0, direction))
        self.ai_confidence = max(0.0, min(1.0, confidence))
        self.ai_action = action
        if regime in REGIMES:
            self.regime = regime
        self.regime_confidence = max(0.0, min(1.0, regime_confidence))

    def ingest_obi(self, obi: float) -> None:
        self._obi.append(float(obi))

    def ingest_volume_delta(self, volume_delta: float) -> None:
        self._vd.append(float(volume_delta))

    def ingest_liquidation(self, notional: float) -> None:
        self.liquidation_notional = float(notional)
        self._liq.append(float(notional))

    def ingest_funding(self, funding_rate: float) -> None:
        self.funding_rate = float(funding_rate)

    def ingest_open_interest(self, oi_change_pct: float) -> None:
        self.oi_change_pct = float(oi_change_pct)

    def ingest_sentinel(self, mode: str) -> None:
        self.sentinel_mode = mode or "NORMAL"

    # -- derived statistics ---------------------------------------------------
    @property
    def mean_obi(self) -> float:
        return _mean(self._obi)

    def volatility(self) -> float:
        """Normalised realised volatility in ``[0, 1]``.

        Blends order-book imbalance dispersion, trade-flow dispersion and the
        recent liquidation cascade. A saturating ``tanh`` keeps it bounded and
        smooth across the very different native scales of each term.
        """
        obi_disp = _stdev(self._obi)                       # OBI in [-1, 1]
        vd_disp = _stdev(self._vd) / self.vd_reference     # normalise flow
        liq_term = min(1.0, self.liquidation_notional / self.liq_reference)
        raw = 2.6 * obi_disp + 1.4 * vd_disp + 0.6 * liq_term + 6.0 * abs(self.funding_rate)
        return math.tanh(raw)

    def sell_pressure(self) -> float:
        """Normalised persistent net selling in ``[0, 1]``.

        Combines the AI ensemble's short conviction with a genuinely negative
        recent order-flow / OBI drift (so a one-off tick can't spike it).
        """
        flow = -_mean(self._vd) / self.vd_reference
        book = -self.mean_obi
        ai_short = max(0.0, -self.ai_direction) * self.ai_confidence
        raw = 0.55 * ai_short + 0.6 * max(0.0, flow) + 0.5 * max(0.0, book)
        return max(0.0, min(1.0, raw))

    def liquidation_ratio(self) -> float:
        return min(1.0, self.liquidation_notional / self.liq_reference)

    def stress(self) -> float:
        """Composite market-stress index in ``[0, 1]``."""
        governance = 0.0 if self.sentinel_mode == "NORMAL" else (
            0.5 if self.sentinel_mode == "REDUCE_ONLY" else 1.0
        )
        raw = (
            0.42 * self.volatility()
            + 0.30 * self.sell_pressure()
            + 0.16 * self.liquidation_ratio()
            + 0.12 * governance
        )
        return max(0.0, min(1.0, raw))

    def snapshot(self) -> MarketSnapshot:
        return MarketSnapshot(
            regime=self.regime,
            regime_confidence=self.regime_confidence,
            ai_direction=self.ai_direction,
            ai_confidence=self.ai_confidence,
            ai_action=self.ai_action,
            volatility=round(self.volatility(), 4),
            sell_pressure=round(self.sell_pressure(), 4),
            liquidation=round(self.liquidation_ratio(), 4),
            stress=round(self.stress(), 4),
            funding_rate=self.funding_rate,
            oi_change_pct=self.oi_change_pct,
            sentinel_mode=self.sentinel_mode,
        )
