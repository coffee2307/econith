"""ECONITH :: bridges.world_bridge

WORLD DOMAIN BRIDGE — coexistence layer between the legacy ``WorldKernel`` and
the advanced :class:`SovereignWorldGraph`.

The current dashboard mutates the world through synchronous REST endpoints
(``/world/tariff``, ``/world/mutate``) that write straight into the legacy
``WorldKernel`` read-model. This bridge preserves that contract while ALSO
dispatching the same structural mutation into the sovereign multi-agent graph,
where it is queued for the next deterministic 5-phase tick — triggering the
stateful butterfly-effect loop and forking the scenario chronology
(``Scenario_A -> Scenario_A.1 -> ...``).

Both systems run on the same :class:`~core.event_bus.EventBus` and the same
:class:`~core.engine.TickPipeline`, but publish on DISTINCT topics
(``world.macro`` for the kernel, ``world.sovereign`` for the graph) so there is
no read-model state pollution.
"""
from __future__ import annotations

import logging
from typing import Any

from ai.simulator_engine.sovereign_graph import SovereignWorldGraph
from ai.simulator_engine.world_kernel import WorldKernel

logger = logging.getLogger("econith.bridges.world")

__all__ = ["WorldBridge"]


class WorldBridge:
    """Fans a single mutation request out to both world engines."""

    def __init__(self, kernel: WorldKernel, graph: SovereignWorldGraph) -> None:
        self._kernel = kernel
        self._graph = graph

    # -- structural mutations -------------------------------------------------
    async def apply_tariff(self, source: str, target: str, value: float) -> dict[str, Any]:
        """Dispatch a tariff mutation to both the legacy kernel and the graph.

        The graph enqueues the tariff for the next tick's ``APPLY_EVENTS`` phase,
        which forks the chronology; the kernel applies it immediately for
        backward compatibility with the current dashboard read-model.
        """
        src, tgt = source.upper(), target.upper()
        # Only fan out an accepted mutation; the graph owns a smaller node set.
        legacy = await self._kernel.set_tariff(src, tgt, value)
        sovereign_queued = bool(legacy.get("ok")) and src in self._graph.nodes and tgt in self._graph.nodes
        if sovereign_queued:
            self._graph.queue_tariff(src, tgt, value)
        # Legacy kernel: immediate mutation for the existing UI.
        active = self._graph.chronology.active_id
        logger.info("tariff bridged %s->%s @ %.2f (chronology active=%s)", src, tgt, value, active)
        return {
            "source": src,
            "target": tgt,
            "value": value,
            "legacy": legacy,
            "sovereign": {
                "queued": sovereign_queued,
                "chronology_active": active,
                "note": (
                    "applied on next 5-phase tick; forks the scenario chronology"
                    if sovereign_queued
                    else "not queued in sovereign graph; see legacy result"
                ),
            },
        }

    async def mutate(self, code: str, group: str, field: str, value: float) -> dict[str, Any]:
        """Dispatch a country field mutation to both engines."""
        c = code.upper()
        legacy = await self._kernel.mutate_country(c, group, field, value)
        queued = bool(legacy.get("ok")) and self._graph.queue_mutation(c, field, value)
        logger.info("mutation bridged %s.%s=%s (sovereign_mapped=%s)", c, field, value, queued)
        return {
            "code": c,
            "field": field,
            "value": value,
            "legacy": legacy,
            "sovereign": {
                "queued": queued,
                "chronology_active": self._graph.chronology.active_id,
            },
        }

    # -- read models ----------------------------------------------------------
    def sovereign_snapshot(self) -> dict[str, Any]:
        """The advanced multi-agent graph read-model."""
        return self._graph.snapshot()

    def chronology(self) -> dict[str, Any]:
        """The stateful scenario chronology tree."""
        return self._graph.chronology.snapshot()
