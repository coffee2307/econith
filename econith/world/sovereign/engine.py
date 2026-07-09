"""ECONITH :: SovereignEngine — TITAN atomic tick orchestrator.

One TickPipeline cycle:
  1. ParallelKernelManager.step_hubs  (50 × 113 vectorized)
  2. CorrelationEngine.propagate      (100 proxies = W @ hubs)
  3. Commit both tensors atomically; publish a single Sentinel snapshot

Does NOT own EventBus publishes — WorldKernel remains the mode-gated publisher.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any

import numpy as np

from econith.base import BaseKernel
from econith.world.sovereign.correlation import CorrelationEngine
from econith.world.sovereign.parallel import HubStepParams, ParallelKernelManager
from econith.world.sovereign.tensor import WorldTensorState
from econith.world.sovereign.topology import FEATURE_DIM, HUB_CODES, N_HUBS, N_PROXIES

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cost
    from econith.world.sovereign.stochastic import StochasticEngine

__all__ = ["SovereignEngine", "TickTelemetry"]


@dataclass(slots=True)
class TickTelemetry:
    tick: int
    hub_ms: float
    proxy_ms: float
    total_ms: float
    n_hubs: int = N_HUBS
    n_proxies: int = N_PROXIES
    feature_dim: int = FEATURE_DIM
    # -- stochastic-realism overhead + regime observability (target < 5 ms) --
    stochastic_ms: float = 0.0     # OU + jump injection cost
    jump_count: int = 0            # jumps fired this tick
    regime_crisis: float = 0.0     # smoothed crisis intensity in [0, 1]
    corr_lambda: float = 0.0       # effective common-factor blend λ_t


class SovereignEngine(BaseKernel):
    """System-scale world stepper: 50 hubs + 100 matrix proxies."""

    def __init__(
        self,
        *,
        state: WorldTensorState | None = None,
        manager: ParallelKernelManager | None = None,
        correlator: CorrelationEngine | None = None,
        stochastic: "StochasticEngine | None" = None,
    ) -> None:
        super().__init__(name="econith.world.sovereign", simulation_only=False)
        self.state = state or WorldTensorState.blank()
        self.manager = manager or ParallelKernelManager(mode="vectorized")
        self.correlator = correlator or CorrelationEngine()
        # Optional stochastic-realism layer (OU + jump diffusion). None => the
        # engine stays fully deterministic (backward compatible).
        self.stochastic = stochastic
        # A correlator exposing update_regime() (DynamicCorrelationEngine) gets
        # its regime advanced each tick; a plain CorrelationEngine does not.
        self._regime_aware = hasattr(self.correlator, "update_regime")
        # Prime proxies so snapshot is valid before first tick.
        self.correlator.propagate(self.state.hubs, out=self.state.proxies)
        self._last_telem: TickTelemetry | None = None

    @property
    def last_telemetry(self) -> TickTelemetry | None:
        return self._last_telem

    def step(
        self,
        *,
        market_stress: float = 0.0,
        scale: float = 1.0,
        external: dict[str, float] | None = None,
    ) -> TickTelemetry:
        """Atomic titan tick. Returns timing telemetry for the stress harness."""
        bias = None
        if external:
            bias = np.zeros(N_HUBS, dtype=np.float64)
            for code, v in external.items():
                if code in HUB_CODES:
                    from econith.world.sovereign.topology import hub_index

                    bias[hub_index(code)] = float(v)

        params = HubStepParams(
            market_stress=float(market_stress),
            scale=float(scale),
            external_bias=bias,
        )

        t0 = perf_counter()
        new_hubs = self.manager.step_hubs(self.state.hubs, params)
        t1 = perf_counter()

        # STOCHASTIC PRE-COMMIT PHASE: OU + jump-diffusion perturbation applied
        # to the fresh (owned) hub buffer BEFORE it is committed. This keeps the
        # whole update — deterministic step, stochastic injection, proxy
        # propagation — inside the single atomic step() call.
        jump_count = 0
        if self.stochastic is not None:
            new_hubs = self.stochastic.apply(
                new_hubs, market_stress=float(market_stress), scale=float(scale)
            )
            jump_count = self.stochastic.last_jumps
        t_stoch = perf_counter()

        # Regime-aware correlator: advance W_t from this tick's stress before the
        # proxy multiply, so crisis correlation spikes land on the same tick.
        regime_crisis = 0.0
        corr_lambda = 0.0
        if self._regime_aware:
            regime = self.correlator.update_regime(float(market_stress))
            regime_crisis = regime.clamped()

        # Atomic commit: swap hub buffer first, then derive proxies into place.
        self.state.hubs = np.ascontiguousarray(new_hubs, dtype=np.float64)
        self.correlator.propagate(self.state.hubs, out=self.state.proxies)
        if self._regime_aware:
            corr_lambda = float(getattr(self.correlator, "last_lambda", 0.0))
        t2 = perf_counter()

        self.state.tick += 1
        telem = TickTelemetry(
            tick=self.state.tick,
            hub_ms=(t1 - t0) * 1_000.0,
            proxy_ms=(t2 - t_stoch) * 1_000.0,
            total_ms=(t2 - t0) * 1_000.0,
            stochastic_ms=(t_stoch - t1) * 1_000.0,
            jump_count=jump_count,
            regime_crisis=regime_crisis,
            corr_lambda=corr_lambda,
        )
        self._last_telem = telem
        return telem

    def snapshot(self) -> dict[str, Any]:
        return self.state.snapshot()

    def close(self) -> None:
        self.manager.close()
