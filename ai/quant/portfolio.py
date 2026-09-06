"""ECONITH :: ai.quant.portfolio

Portfolio-aware capital allocation + correlation-aware risk intelligence.

Two cooperating components upgrade the execution pipeline from single-asset to
portfolio-level:

  * :class:`PortfolioAllocator` — divides trading equity across the isolated
    asset desks (CRYPTO_MAJORS / CRYPTO_HIGH_BETA / CRYPTO_MEME / ...) using a
    conviction- and volatility-aware scheme with hard per-desk exposure caps.
  * :class:`PortfolioRiskModel` — computes a correlation-aware portfolio VaR from
    the covariance of active positions and derives a global de-risking scalar the
    Sentinel applies during high-stress covariance spikes.

Pure NumPy, zero ML dependencies. Every routine is guarded against NaN/Inf,
empty inputs, singular covariance and zero-division so a degenerate portfolio
state never raises.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("econith.ai.quant.portfolio")

__all__ = [
    "DeskAllocation",
    "PortfolioAllocator",
    "PortfolioState",
    "PortfolioRiskModel",
]

# Desk taxonomy + default risk budget (fraction of equity). Mirrors AssetUniverse
# but kept local so this module has no heavy runtime import.
DEFAULT_DESK_BUDGET: dict[str, float] = {
    "crypto_majors": 0.50,
    "crypto_high_beta": 0.30,
    "crypto_meme": 0.10,
    "tradfi_forex": 0.05,
    "commodities": 0.05,
}

# Hard per-desk exposure caps (fraction of equity) — never exceeded regardless
# of conviction. Meme desks are structurally capped tighter.
DESK_EXPOSURE_CAP: dict[str, float] = {
    "crypto_majors": 0.60,
    "crypto_high_beta": 0.35,
    "crypto_meme": 0.12,
    "tradfi_forex": 0.10,
    "commodities": 0.10,
}


def _finite(x: float, default: float = 0.0) -> float:
    return x if isinstance(x, (int, float)) and math.isfinite(x) else default


@dataclass(slots=True)
class DeskAllocation:
    """Resolved capital allocation for one desk."""

    desk: str
    target_weight: float          # fraction of total equity [0, cap]
    notional: float               # target_weight * equity
    conviction: float             # [0, 1] driver used
    capped: bool                  # True if the exposure cap bound the weight

    def to_dict(self) -> dict[str, float | str | bool]:
        return {
            "desk": self.desk,
            "target_weight": round(self.target_weight, 6),
            "notional": round(self.notional, 2),
            "conviction": round(self.conviction, 4),
            "capped": self.capped,
        }


class PortfolioAllocator:
    """Divides equity across desks by conviction, budget and hard caps."""

    def __init__(
        self,
        *,
        desk_budget: Optional[dict[str, float]] = None,
        exposure_cap: Optional[dict[str, float]] = None,
        gross_leverage: float = 1.0,
    ) -> None:
        self._budget = dict(desk_budget or DEFAULT_DESK_BUDGET)
        self._cap = dict(exposure_cap or DESK_EXPOSURE_CAP)
        self._gross_leverage = max(0.0, _finite(gross_leverage, 1.0))

    def allocate(
        self,
        equity: float,
        conviction: dict[str, float],
        *,
        risk_scalar: float = 1.0,
    ) -> list[DeskAllocation]:
        """Allocate ``equity`` across desks.

        ``conviction[desk]`` in [0, 1] scales that desk toward its budget; the
        global ``risk_scalar`` in [0, 1] (from :class:`PortfolioRiskModel`)
        uniformly de-risks the whole book during stress.
        """
        equity = max(0.0, _finite(equity, 0.0))
        risk_scalar = min(1.0, max(0.0, _finite(risk_scalar, 1.0)))

        # Raw desk scores = budget * conviction.
        scores: dict[str, float] = {}
        for desk, budget in self._budget.items():
            conv = min(1.0, max(0.0, _finite(conviction.get(desk, 0.0), 0.0)))
            scores[desk] = max(0.0, _finite(budget, 0.0)) * conv

        total_score = sum(scores.values())
        allocations: list[DeskAllocation] = []
        for desk, score in scores.items():
            # Normalise scores into weights, then apply gross leverage + risk scalar.
            base_weight = (score / total_score) if total_score > 0 else 0.0
            weight = base_weight * self._gross_leverage * risk_scalar
            cap = _finite(self._cap.get(desk, 1.0), 1.0)
            capped = weight > cap
            weight = min(weight, cap)
            allocations.append(
                DeskAllocation(
                    desk=desk,
                    target_weight=weight,
                    notional=weight * equity,
                    conviction=min(1.0, max(0.0, _finite(conviction.get(desk, 0.0), 0.0))),
                    capped=capped,
                )
            )
        return allocations


@dataclass(slots=True)
class PortfolioState:
    """Snapshot of active positions for the risk model.

    ``symbols`` and ``weights`` are parallel arrays (weight = signed fraction of
    equity per symbol). ``returns_matrix`` is an optional (T x N) historical
    return matrix used to estimate the correlation/covariance structure.
    """

    symbols: list[str] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    returns_matrix: Optional[list[list[float]]] = None


class PortfolioRiskModel:
    """Correlation-aware portfolio VaR + dynamic de-risking scalar."""

    def __init__(
        self,
        *,
        confidence: float = 0.99,
        var_limit_pct: float = 0.05,
        min_periods: int = 20,
    ) -> None:
        # One-tailed z for the confidence level (0.99 -> 2.326).
        self._z = self._z_score(confidence)
        self._var_limit = max(1e-4, _finite(var_limit_pct, 0.05))
        self._min_periods = max(2, min_periods)

    @staticmethod
    def _z_score(confidence: float) -> float:
        table = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.9600, 0.99: 2.3263, 0.995: 2.5758}
        # Nearest tabulated confidence keeps us dependency-free (no scipy).
        key = min(table, key=lambda c: abs(c - confidence))
        return table[key]

    def covariance(self, state: PortfolioState) -> Optional[Any]:
        """Estimate the (N x N) covariance from the return matrix, or None."""
        import numpy as np

        if not state.returns_matrix:
            return None
        mat = np.asarray(state.returns_matrix, dtype="float64")
        mat = np.nan_to_num(mat, nan=0.0, posinf=0.0, neginf=0.0)
        if mat.ndim != 2 or mat.shape[0] < self._min_periods or mat.shape[1] == 0:
            return None
        # rowvar=False: columns are assets. ddof=1 for sample covariance.
        cov = np.cov(mat, rowvar=False, ddof=1)
        # np.cov returns a scalar for a single asset; promote to 1x1.
        return np.atleast_2d(cov)

    def parametric_var(self, state: PortfolioState) -> float:
        """Correlation-aware 1-step parametric VaR as a fraction of equity.

        VaR = z * sqrt(wᵀ Σ w). Falls back to a diagonal (uncorrelated) estimate
        when a covariance matrix is unavailable, and to 0.0 when there is nothing
        at risk.
        """
        import numpy as np

        w = np.nan_to_num(np.asarray(state.weights, dtype="float64"), nan=0.0)
        if w.size == 0 or not np.any(w):
            return 0.0

        cov = self.covariance(state)
        if cov is None or cov.shape[0] != w.size:
            # No covariance history: assume a modest uncorrelated per-asset vol so
            # the check is still meaningful (2% per-step sigma placeholder).
            sigma = 0.02
            variance = float(np.sum((w * sigma) ** 2))
        else:
            variance = float(w @ cov @ w)
        variance = max(0.0, variance)
        return float(self._z * math.sqrt(variance))

    def average_correlation(self, state: PortfolioState) -> float:
        """Mean pairwise correlation of active positions (0 when undefined)."""
        import numpy as np

        cov = self.covariance(state)
        if cov is None or cov.shape[0] < 2:
            return 0.0
        d = np.sqrt(np.clip(np.diag(cov), 1e-12, None))
        denom = np.outer(d, d)
        corr = np.divide(cov, denom, out=np.zeros_like(cov), where=denom > 0)
        n = corr.shape[0]
        off_diag = corr[~np.eye(n, dtype=bool)]
        if off_diag.size == 0:
            return 0.0
        return float(np.clip(np.mean(off_diag), -1.0, 1.0))

    def derisk_scalar(self, state: PortfolioState) -> float:
        """Global position-sizing scalar in [0, 1].

        When portfolio VaR exceeds the limit, shrink proportionally so the scaled
        book sits at the limit. A high average correlation (covariance spike)
        applies an additional multiplicative haircut, because diversification has
        collapsed and tail risk is concentrated.
        """
        var = self.parametric_var(state)
        base = 1.0 if var <= self._var_limit else max(0.05, self._var_limit / var)

        corr = self.average_correlation(state)
        # Only positive co-movement is dangerous; map [0.5, 1.0] -> haircut up to 40%.
        corr_haircut = 1.0 - 0.4 * max(0.0, (corr - 0.5) / 0.5) if corr > 0.5 else 1.0
        return float(max(0.05, min(1.0, base * corr_haircut)))

    def assess(self, state: PortfolioState) -> dict[str, float]:
        """Full risk read-model for telemetry + Sentinel consumption."""
        return {
            "portfolio_var": round(self.parametric_var(state), 6),
            "var_limit": round(self._var_limit, 6),
            "avg_correlation": round(self.average_correlation(state), 4),
            "derisk_scalar": round(self.derisk_scalar(state), 4),
            "num_positions": int(sum(1 for w in state.weights if abs(_finite(w)) > 1e-9)),
        }
