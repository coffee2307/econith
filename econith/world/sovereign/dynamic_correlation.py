"""ECONITH :: econith.world.sovereign.dynamic_correlation — regime-aware W_t.

The base :class:`CorrelationEngine` derives every proxy row from a **static**
sparse weight matrix ``W`` (``proxies = W @ hubs``). That is fine in calm
markets, but it is unrealistic in a crisis: real cross-asset correlations are
*state dependent*. When systemic stress rises, diversification breaks down and
"everything drops together" — the correlation matrix collapses toward a single
common factor.

:class:`DynamicCorrelationEngine` keeps the cheap static base multiply and adds
a **regime blend** toward the cross-sectional common factor:

    proxies = (1 − λ_t) · (W @ hubs)  +  λ_t · common_factor

    λ_t          = clip(base_beta + crisis_beta · crisis_intensity, 0, λ_max)
    common_factor = mean over hubs (broadcast to every proxy)

In *stability* (``crisis_intensity → 0``) λ_t is small → proxies keep their
idiosyncratic, weakly-correlated behaviour. In *crisis*
(``crisis_intensity → 1``) λ_t spikes → all proxies converge toward the shared
market factor, so their pairwise correlation jumps. The extra cost is one
``mean`` + one broadcast blend — O(N_PROXIES × F), well inside the tick budget.

Signature parity: ``propagate(hubs, out=...)`` is unchanged, so this class is a
**drop-in** for the ``correlator`` argument of :class:`SovereignEngine`. The
regime is updated out-of-band via :meth:`update_regime` each tick.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from econith.world.sovereign.correlation import CorrelationEngine, SparseCorr
from econith.world.sovereign.topology import FEATURE_DIM, N_HUBS, N_PROXIES

__all__ = ["RegimeLabel", "RegimeState", "DynamicCorrelationEngine"]


class RegimeLabel(str, Enum):
    STABILITY = "STABILITY"
    STRESS = "STRESS"
    CRISIS = "CRISIS"


@dataclass(slots=True)
class RegimeState:
    """Continuous systemic-stress state driving the correlation blend."""

    intensity: float = 0.0   # 0 (calm) .. 1 (full crisis)

    @property
    def label(self) -> RegimeLabel:
        if self.intensity >= 0.6:
            return RegimeLabel.CRISIS
        if self.intensity >= 0.3:
            return RegimeLabel.STRESS
        return RegimeLabel.STABILITY

    def clamped(self) -> float:
        return float(min(1.0, max(0.0, self.intensity)))


class DynamicCorrelationEngine(CorrelationEngine):
    """Correlation engine with a regime-dependent effective weight matrix W_t."""

    def __init__(
        self,
        matrix: SparseCorr | None = None,
        *,
        base_beta: float = 0.05,
        crisis_beta: float = 0.75,
        lambda_max: float = 0.85,
        smoothing: float = 0.5,
    ) -> None:
        super().__init__(matrix)
        self._base_beta = float(base_beta)
        self._crisis_beta = float(crisis_beta)
        self._lambda_max = float(lambda_max)
        self._smoothing = float(min(1.0, max(0.0, smoothing)))
        self._regime = RegimeState(0.0)
        self._crisis = 0.0          # EWMA-smoothed crisis intensity
        self._last_lambda = 0.0

    # -- regime control -------------------------------------------------------
    def update_regime(self, market_stress: float) -> RegimeState:
        """Advance the regime from the tick's market stress (EWMA-smoothed).

        Smoothing prevents the correlation matrix from flickering tick-to-tick;
        crises build and decay rather than teleport.
        """
        target = float(min(1.0, max(0.0, market_stress)))
        self._crisis = (
            self._smoothing * self._crisis + (1.0 - self._smoothing) * target
        )
        self._regime = RegimeState(self._crisis)
        return self._regime

    @property
    def regime(self) -> RegimeState:
        return self._regime

    @property
    def last_lambda(self) -> float:
        return self._last_lambda

    def _lambda(self) -> float:
        lam = self._base_beta + self._crisis_beta * self._regime.clamped()
        return float(min(self._lambda_max, max(0.0, lam)))

    # -- hot path (signature-compatible with CorrelationEngine) ---------------
    def propagate(self, hubs: np.ndarray, out: np.ndarray | None = None) -> np.ndarray:
        if hubs.shape != (N_HUBS, FEATURE_DIM):
            raise ValueError(f"hubs shape {hubs.shape}")

        # 1) Static idiosyncratic base: proxies = W @ hubs.
        derived = self._W.multiply(hubs)  # (N_PROXIES, F)

        # 2) Regime blend toward the cross-sectional common factor.
        lam = self._lambda()
        self._last_lambda = lam
        if lam > 1e-6:
            common = hubs.mean(axis=0)  # (F,) shared market factor
            # derived += λ · (common − derived)  == (1−λ)·derived + λ·common
            derived += lam * (common - derived)

        if out is None:
            return np.ascontiguousarray(derived, dtype=np.float64)
        if self._frozen.any():
            mask = ~self._frozen
            out[mask] = derived[mask]
        else:
            np.copyto(out, derived)
        return out
