"""ECONITH :: core.engine

The AI-001 Core Engine -- an independent foundation, decoupled from the Trading
(econith_quant) and Simulator (ECONITH World) components, both of which plug into
it via the EventBus.

This module now ships a **rigid, deterministic, tick-based game-engine loop**.
Every simulated day (one ``TimeEngine`` tick at 1x == 1 real second) the
:class:`TickPipeline` executes exactly five phases, strictly in order, each fully
awaited before the next begins so there are NO data races within a tick:

    PHASE 1  SNAPSHOT STATE     freeze the authoritative state for the tick
    PHASE 2  APPLY EVENTS       drain queued anomalies / REST mutations / scenarios
    PHASE 3  RESOLVE CONFLICTS  adjudicate competing vectors (Sentinel veto wins)
    PHASE 4  UPDATE WORLD       compute behavioural physics + propagate causality
    PHASE 5  EMIT SIGNALS       pack the unified telemetry matrix + broadcast (5Hz)

Backward compatibility: the pipeline still publishes ``time.tick`` (in PHASE 4)
so every existing subscriber -- notably ``WorldKernel._on_tick`` -- keeps firing
unchanged. Subsystems may additionally register *phase handlers* to participate
in the deterministic loop directly.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from config.settings import TIME_SPEED_MULTIPLIERS, get_settings
from core.event_bus import EventBus

logger = logging.getLogger("econith.core.engine")


# ===========================================================================
#  State engine
# ===========================================================================
@dataclass
class StateEngine:
    """Authoritative, in-memory snapshot of the simulated world/system."""

    state: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.state[key] = value

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)


# ===========================================================================
#  Deterministic tick pipeline
# ===========================================================================
class TickPhase(IntEnum):
    """The five strictly-ordered phases of a single deterministic tick."""

    SNAPSHOT = 1        # freeze current state (no mutation)
    APPLY_EVENTS = 2    # inject queued anomalies / REST mutations / scenarios
    RESOLVE_CONFLICTS = 3   # adjudicate competing vectors (Sentinel veto wins)
    UPDATE_WORLD = 4    # behavioural physics + causal propagation
    EMIT_SIGNALS = 5    # pack unified telemetry + broadcast


@dataclass
class PendingEvent:
    """A discrete instruction queued for injection during PHASE 2.

    ``kind`` classifies the source (``anomaly`` / ``mutation`` / ``scenario`` /
    ``tariff`` ...); ``payload`` carries its data; ``priority`` breaks ordering
    ties (lower first) so injection is deterministic regardless of arrival order.
    """

    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 100
    seq: int = 0        # monotonic tiebreaker assigned on enqueue


@dataclass
class Veto:
    """A conflict-resolution verdict raised during PHASE 3.

    The canonical use is the Sentinel overriding an AI trade intent: when a veto
    with a higher ``authority`` targets the same ``domain`` as a proposed action,
    the action is suppressed. Sentinel vetoes carry the highest authority.
    """

    source: str
    domain: str                 # e.g. "trade", "micro_impact", "policy"
    authority: int = 100        # higher wins
    reason: str = ""


@dataclass
class TickContext:
    """Per-tick shared blackboard threaded through all five phases.

    A single instance is created at the top of every tick and handed to each
    phase handler in turn, so state produced in an earlier phase is visible to
    later ones within the same tick -- and discarded when the tick ends.
    """

    sim_day: int
    multiplier: int
    tick_index: int
    started_at: float
    scale: float = 1.0
    frozen_state: dict[str, Any] = field(default_factory=dict)      # PHASE 1
    events: list[PendingEvent] = field(default_factory=list)        # PHASE 2
    vetoes: list[Veto] = field(default_factory=list)                # PHASE 3
    signals: dict[str, Any] = field(default_factory=dict)           # PHASE 5

    # -- conflict helpers -----------------------------------------------------
    def raise_veto(self, source: str, domain: str, authority: int = 100,
                   reason: str = "") -> None:
        self.vetoes.append(Veto(source, domain, authority, reason))

    def is_vetoed(self, domain: str, below_authority: int = 10_000) -> bool:
        """True if any veto targets ``domain`` with authority < ``below_authority``.

        Callers proposing an action pass their own authority; a higher-authority
        veto on the same domain suppresses them.
        """
        return any(
            v.domain == domain and v.authority >= below_authority
            for v in self.vetoes
        )

    def veto_authority(self, domain: str) -> int:
        """Highest veto authority currently standing against ``domain`` (0 if none)."""
        relevant = [v.authority for v in self.vetoes if v.domain == domain]
        return max(relevant) if relevant else 0

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000.0


PhaseHandler = Callable[[TickContext], Awaitable[None]]


class TickPipeline:
    """Deterministic five-phase executor driven by the :class:`TimeEngine`.

    Handlers register against a :class:`TickPhase` with a priority; within a
    phase they run in ascending priority order, each fully awaited before the
    next, guaranteeing a reproducible, race-free execution order every tick.
    """

    _ORDER: tuple[TickPhase, ...] = (
        TickPhase.SNAPSHOT,
        TickPhase.APPLY_EVENTS,
        TickPhase.RESOLVE_CONFLICTS,
        TickPhase.UPDATE_WORLD,
        TickPhase.EMIT_SIGNALS,
    )

    def __init__(self, bus: EventBus, state: StateEngine | None = None) -> None:
        self._bus = bus
        self._state = state or StateEngine()
        self._handlers: dict[TickPhase, list[tuple[int, PhaseHandler]]] = defaultdict(list)
        self._pending: list[PendingEvent] = []
        self._seq = 0
        self._tick_index = 0
        self._last_ms = 0.0

    # -- registration ---------------------------------------------------------
    def register(self, phase: TickPhase, handler: PhaseHandler, priority: int = 100) -> None:
        """Attach ``handler`` to ``phase``; lower ``priority`` runs earlier."""
        self._handlers[phase].append((priority, handler))
        self._handlers[phase].sort(key=lambda t: t[0])
        logger.debug("registered handler on phase %s (prio %d)", phase.name, priority)

    def enqueue(self, kind: str, priority: int = 100, **payload: Any) -> None:
        """Queue an event for deterministic injection in PHASE 2 of the next tick."""
        self._seq += 1
        self._pending.append(PendingEvent(kind=kind, payload=payload,
                                          priority=priority, seq=self._seq))

    # -- execution ------------------------------------------------------------
    async def run_tick(self, sim_day: int, multiplier: int) -> TickContext:
        """Execute the five phases for one simulated day, strictly in order."""
        self._tick_index += 1
        ctx = TickContext(
            sim_day=sim_day,
            multiplier=multiplier,
            tick_index=self._tick_index,
            started_at=time.perf_counter(),
            scale=min(max(multiplier, 1), 20) ** 0.5,
        )

        # PHASE 2 pre-load: hand the drained queue to the context so event
        # appliers see a frozen, deterministic ordering for this tick.
        ctx.events = self._drain_pending()

        for phase in self._ORDER:
            await self._run_phase(phase, ctx)

        self._last_ms = ctx.elapsed_ms()
        self._state.set("last_tick", {
            "sim_day": sim_day,
            "tick_index": self._tick_index,
            "duration_ms": round(self._last_ms, 3),
            "events_applied": len(ctx.events),
            "vetoes": len(ctx.vetoes),
        })
        return ctx

    async def _run_phase(self, phase: TickPhase, ctx: TickContext) -> None:
        # Built-in phase behaviour first, then any registered handlers.
        if phase is TickPhase.SNAPSHOT:
            ctx.frozen_state = self._state.snapshot()
        elif phase is TickPhase.UPDATE_WORLD:
            # Backward-compat spine: drive every legacy ``time.tick`` subscriber.
            await self._bus.publish(
                "time.tick", sim_day=ctx.sim_day, multiplier=ctx.multiplier,
                tick_index=ctx.tick_index,
            )

        for _prio, handler in self._handlers.get(phase, ()):  # deterministic order
            try:
                await handler(ctx)
            except Exception:  # noqa: BLE001 - a faulty handler must not desync the loop
                logger.exception("phase %s handler failed on day %d", phase.name, ctx.sim_day)

        if phase is TickPhase.EMIT_SIGNALS:
            await self._bus.publish(
                "engine.tick_complete",
                sim_day=ctx.sim_day,
                tick_index=ctx.tick_index,
                duration_ms=round(ctx.elapsed_ms(), 3),
                events_applied=len(ctx.events),
                vetoes=len(ctx.vetoes),
                **ctx.signals,
            )

    def _drain_pending(self) -> list[PendingEvent]:
        if not self._pending:
            return []
        drained = sorted(self._pending, key=lambda e: (e.priority, e.seq))
        self._pending = []
        return drained

    # -- introspection --------------------------------------------------------
    @property
    def last_duration_ms(self) -> float:
        return self._last_ms

    @property
    def tick_index(self) -> int:
        return self._tick_index


# ===========================================================================
#  Time engine
# ===========================================================================
class TimeEngine:
    """Simulated time driver.

    Contract (master plan, Phase 0): at multiplier 1x, every 1 real-world second
    advances the simulation by exactly 1 day. Each advance runs one full
    deterministic :class:`TickPipeline` cycle when a pipeline is attached.
    """

    def __init__(
        self,
        bus: EventBus,
        multiplier: int = 1,
        pipeline: TickPipeline | None = None,
    ) -> None:
        self._bus = bus
        self._multiplier = self._validate(multiplier)
        self._pipeline = pipeline
        self._sim_day = 0
        self._running = False
        self._paused = False
        self._task: asyncio.Task[None] | None = None

    @staticmethod
    def _validate(multiplier: int) -> int:
        if multiplier not in TIME_SPEED_MULTIPLIERS:
            raise ValueError(
                f"speed {multiplier}x not allowed; choose one of {TIME_SPEED_MULTIPLIERS}"
            )
        return multiplier

    @property
    def multiplier(self) -> int:
        return self._multiplier

    @property
    def sim_day(self) -> int:
        return self._sim_day

    @property
    def running(self) -> bool:
        """True when the clock is started AND not paused."""
        return self._running and not self._paused

    @property
    def pipeline(self) -> TickPipeline | None:
        return self._pipeline

    def attach_pipeline(self, pipeline: TickPipeline) -> None:
        self._pipeline = pipeline

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_speed(self, multiplier: int) -> None:
        self._multiplier = self._validate(multiplier)
        logger.info("time speed set to %dx", self._multiplier)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._tick_loop(), name="time-engine")
        logger.info("time engine started at %dx", self._multiplier)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _tick_loop(self) -> None:
        # One real second == `multiplier` simulated days.
        while self._running:
            await asyncio.sleep(1.0)
            if self._paused:
                continue
            self._sim_day += self._multiplier
            if self._pipeline is not None:
                # Deterministic 5-phase execution (SNAPSHOT..EMIT).
                await self._pipeline.run_tick(self._sim_day, self._multiplier)
            else:
                # Fallback: raw tick for any pipeline-less TimeEngine.
                await self._bus.publish(
                    "time.tick", sim_day=self._sim_day, multiplier=self._multiplier
                )


# ===========================================================================
#  Engine orchestrator
# ===========================================================================
class Engine:
    """Top-level orchestrator that owns the bus, state, pipeline and clock."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.bus = EventBus()
        self.state = StateEngine()
        self.pipeline = TickPipeline(self.bus, self.state)
        self.time = TimeEngine(self.bus, multiplier=1, pipeline=self.pipeline)
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return
        await self.bus.start()
        await self.time.start()
        self._started = True
        logger.info("%s core engine online (deterministic tick pipeline)", self.settings.app_name)

    async def shutdown(self) -> None:
        await self.time.stop()
        await self.bus.stop()
        self._started = False
        logger.info("core engine offline")


_engine: Engine | None = None


def get_engine() -> Engine:
    """Singleton accessor for the Core Engine."""
    global _engine
    if _engine is None:
        _engine = Engine()
    return _engine
