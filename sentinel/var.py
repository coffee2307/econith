"""ECONITH :: sentinel.var

Value-at-Risk (VaR) and Conditional VaR (C-VaR / Expected Shortfall) via
Historical Simulation and Monte-Carlo simulation (master plan, Phase 3, Step 1).

VaR(c)  = the loss threshold not exceeded with confidence ``c`` over the horizon.
C-VaR(c) = the *expected* loss given that the VaR threshold is breached (tail mean).

The model maintains a rolling buffer of realised per-tick log-returns. When the
buffer is thin it augments the empirical distribution with a Monte-Carlo sample
drawn from the current realised volatility, so a usable risk number is always
available. Returns are scaled to a 24h horizon via the square-root-of-time rule.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import deque
from dataclasses import dataclass

# How many tick-intervals we treat as a "24h" horizon for the sqrt-time scale.
DEFAULT_HORIZON_STEPS = 240
MIN_SAMPLES = 30
MC_SCENARIOS = 10_000


@dataclass(slots=True, frozen=True)
class RiskEstimate:
    var: float          # positive number = fractional loss (e.g. 0.031 == 3.1%)
    cvar: float
    confidence: float
    horizon_steps: int
    method: str
    sample_size: int


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = pct / 100.0 * (len(sorted_values) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    frac = rank - low
    return sorted_values[low] * (1 - frac) + sorted_values[high] * frac


def historical_var(returns: list[float], confidence: float = 0.99) -> tuple[float, float]:
    """VaR & C-VaR from an empirical return distribution.

    Losses are ``-return``; we look at the upper tail of the loss distribution.
    """
    if not returns:
        return 0.0, 0.0
    losses = sorted(-r for r in returns)            # ascending losses
    var = _percentile(losses, confidence * 100.0)
    tail = [loss for loss in losses if loss >= var]
    cvar = statistics.fmean(tail) if tail else var
    return max(var, 0.0), max(cvar, 0.0)


def monte_carlo_var(
    mu: float,
    sigma: float,
    confidence: float = 0.99,
    scenarios: int = MC_SCENARIOS,
) -> tuple[float, float]:
    """VaR & C-VaR from a Monte-Carlo normal sample (10k scenarios by default)."""
    sample = [random.gauss(mu, sigma) for _ in range(scenarios)]
    return historical_var(sample, confidence)


class HistoricalSimulationVaR:
    """Rolling VaR/C-VaR engine fed one realised return at a time."""

    def __init__(
        self,
        window: int = 2_000,
        horizon_steps: int = DEFAULT_HORIZON_STEPS,
        confidence: float = 0.99,
    ) -> None:
        self._returns: deque[float] = deque(maxlen=window)
        self._horizon_steps = horizon_steps
        self._confidence = confidence

    def update(self, ret: float) -> None:
        if ret == ret and abs(ret) != math.inf:  # drop NaN/inf
            self._returns.append(ret)

    @property
    def realised_sigma(self) -> float:
        if len(self._returns) < 2:
            return 0.0
        return statistics.pstdev(self._returns)

    def estimate(self, confidence: float | None = None) -> RiskEstimate:
        c = confidence if confidence is not None else self._confidence
        scale = math.sqrt(self._horizon_steps)   # sqrt-of-time to 24h horizon
        n = len(self._returns)

        if n >= MIN_SAMPLES:
            # Empirical historical simulation, scaled to the horizon.
            scaled = [r * scale for r in self._returns]
            var, cvar = historical_var(scaled, c)
            method = "historical"
        else:
            # Thin buffer -> Monte-Carlo from current realised vol.
            sigma = max(self.realised_sigma, 1e-4) * scale
            var, cvar = monte_carlo_var(mu=0.0, sigma=sigma, confidence=c)
            method = "monte_carlo"

        return RiskEstimate(
            var=var,
            cvar=cvar,
            confidence=c,
            horizon_steps=self._horizon_steps,
            method=method,
            sample_size=n,
        )
