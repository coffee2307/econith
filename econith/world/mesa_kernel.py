"""ECONITH :: econith.world.mesa_kernel

Native sovereign step kernel. At TITAN scale the vectorized SovereignEngine
owns the 50-hub tensor; this Mesa adapter remains the object-entity bridge
used by the classic 6-hub WorldKernel path.
"""
from __future__ import annotations

from typing import Any

from econith.base import BaseKernel

__all__ = ["MesaSovereignKernel"]


class MesaSovereignKernel(BaseKernel):
    """Single-tick sovereign behaviour solver.

    Accepts the country entity matrix and returns a flat list of Adjustment
    proposals for this tick. No internal scheduler thread, no global state.
    """

    def __init__(self) -> None:
        super().__init__(name="econith.world.mesa", simulation_only=False)
        self._titan: Any = None

    def attach_titan(self, engine: Any) -> None:
        """Optional hook: bind a SovereignEngine for tensor co-stepping."""
        self._titan = engine

    def step(
        self,
        *,
        entities: dict[str, Any],
        external: dict[str, float],
        market_stress: float,
        scale: float,
    ) -> list[Any]:
        # TITAN path — advance the system-scale tensor in lockstep (no Adjustment
        # objects; WorldKernel still applies classic entity proposals).
        if self._titan is not None:
            try:
                self._titan.step(
                    market_stress=market_stress,
                    scale=scale,
                    external=external,
                )
            except Exception:  # noqa: BLE001
                pass

        out: list[Any] = []
        for code, ent in entities.items():
            out.extend(ent.calculate_behavior(market_stress, external.get(code, 0.0), scale))
        return out
