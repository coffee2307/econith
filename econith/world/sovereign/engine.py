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
from typing import Any

import numpy as np

from econith.base import BaseKernel
from econith.world.sovereign.correlation import CorrelationEngine
from econith.world.sovereign.parallel import HubStepParams, ParallelKernelManager
from econith.world.sovereign.tensor import WorldTensorState
from econith.world.sovereign.topology import FEATURE_DIM, HUB_CODES, N_HUBS, N_PROXIES

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


class SovereignEngine(BaseKernel):
    """System-scale world stepper: 50 hubs + 100 matrix proxies."""

    def __init__(
        self,
        *,
        state: WorldTensorState | None = None,
        manager: ParallelKernelManager | None = None,
        correlator: CorrelationEngine | None = None,
    ) -> None:
        super().__init__(name="econith.world.sovereign", simulation_only=False)
        self.state = state or WorldTensorState.blank()
        self.manager = manager or ParallelKernelManager(mode="vectorized")
        self.correlator = correlator or CorrelationEngine()
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
        # Atomic commit: swap hub buffer first, then derive proxies into place.
        self.state.hubs = np.ascontiguousarray(new_hubs, dtype=np.float64)
        self.correlator.propagate(self.state.hubs, out=self.state.proxies)
        t2 = perf_counter()

        self.state.tick += 1
        telem = TickTelemetry(
            tick=self.state.tick,
            hub_ms=(t1 - t0) * 1_000.0,
            proxy_ms=(t2 - t1) * 1_000.0,
            total_ms=(t2 - t0) * 1_000.0,
        )
        self._last_telem = telem
        return telem

    def snapshot(self) -> dict[str, Any]:
        return self.state.snapshot()

    def close(self) -> None:
        self.manager.close()
