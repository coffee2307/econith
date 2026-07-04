"""ECONITH :: ai.meta.core_ai

The Core AI Orchestrator — the apex of the multi-agent inference runtime.

Architectural pattern
----------------------
This is **not** a single omniscient model. It is a *coordination brain*: an
EventBus consumer that maintains a unified :class:`CrossAssetContext` fusing two
very different clocks —

  * HIGH-FREQUENCY plane: coin order-flow (``md.ticker``, ``indicator.obi``,
    ``indicator.volume_delta``, ``alt.*``) arriving many times per second.
  * LOW-FREQUENCY plane: macro / tradfi shifts (``core.macro.context``) arriving
    minutes-to-days apart.

On a fixed cadence it derives three **directives** from the fused context and
publishes them so each sub-agent recalibrates without being tightly coupled to
the others:

  * :class:`QuantDirective`  -> ``meta.quant.directive``   (signal bias / risk appetite)
  * :class:`RiskDirective`   -> ``meta.risk.directive``    (dynamic Sentinel thresholds)
  * :class:`WorldDirective`  -> ``meta.world.directive``   (scenario pressure)

Sub-agents *subscribe* to their directive topic and treat it as advisory: the
orchestrator steers, it does not seize control (the Sentinel still holds the
hard veto). This keeps the system loosely coupled and independently testable.

The orchestrator is mode-aware: in REALITY it derives directives from live data
only; in SIMULATION it also folds World coupling. It never blocks and never
raises into the bus dispatch loop.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.event_bus import Event, EventBus
from core.mode import QuantMode, get_mode_manager

logger = logging.getLogger("econith.ai.meta.core_ai")

__all__ = [
    "CrossAssetContext",
    "QuantDirective",
    "RiskDirective",
    "WorldDirective",
    "CoreAIOrchestrator",
]


# ---------------------------------------------------------------------------
# Unified cross-asset context (the fused state)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class CrossAssetContext:
    """The single fused view the orchestrator reasons over.

    High-frequency micro fields are overwritten on every coin tick; low-frequency
    macro fields change rarely. ``updated_ms`` timestamps the last mutation so
    downstream logic can reason about staleness.
    """

    # -- high-frequency micro plane --
    price: Optional[float] = None
    obi: Optional[float] = None                 # order-book imbalance [-1, 1]
    volume_delta: Optional[float] = None
    funding_rate: Optional[float] = None
    realized_vol: float = 0.0                   # rolling |return| proxy

    # -- low-frequency macro plane --
    fed_funds_rate: Optional[float] = None
    dollar_index: Optional[float] = None
    yield_spread_10y_2y: Optional[float] = None
    macro_regime: str = "UNKNOWN"

    # -- world coupling (SIMULATION only) --
    world_shock: float = 0.0

    updated_ms: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "obi": self.obi,
            "volume_delta": self.volume_delta,
            "funding_rate": self.funding_rate,
            "realized_vol": round(self.realized_vol, 6),
            "fed_funds_rate": self.fed_funds_rate,
            "dollar_index": self.dollar_index,
            "yield_spread_10y_2y": self.yield_spread_10y_2y,
            "macro_regime": self.macro_regime,
            "world_shock": round(self.world_shock, 4),
            "updated_ms": self.updated_ms,
        }


# ---------------------------------------------------------------------------
# Directives (what the orchestrator hands each sub-agent)
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class QuantDirective:
    """Advisory recalibration for the Quant signal engine."""

    risk_appetite: float           # [0, 1] scales position sizing / conviction
    directional_bias: float        # [-1, 1] macro-informed long/short lean
    regime_hint: str
    rationale: str

    def payload(self) -> dict[str, Any]:
        return {
            "risk_appetite": round(self.risk_appetite, 4),
            "directional_bias": round(self.directional_bias, 4),
            "regime_hint": self.regime_hint,
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class RiskDirective:
    """Dynamic Sentinel threshold recalibration."""

    max_drawdown_pct: float
    var_limit_pct: float
    latency_limit_ms: float
    rationale: str

    def payload(self) -> dict[str, Any]:
        return {
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "var_limit_pct": round(self.var_limit_pct, 4),
            "latency_limit_ms": round(self.latency_limit_ms, 1),
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class WorldDirective:
    """Scenario pressure fed back to the World simulator (SIMULATION only)."""

    volatility_pressure: float     # [0, 1] how turbulent the world should run
    macro_regime: str
    rationale: str

    def payload(self) -> dict[str, Any]:
        return {
            "volatility_pressure": round(self.volatility_pressure, 4),
            "macro_regime": self.macro_regime,
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# The orchestrator
# ---------------------------------------------------------------------------
class CoreAIOrchestrator:
    """Fuses multi-frequency context and steers the sub-agents on a cadence."""

    def __init__(
        self,
        bus: EventBus,
        *,
        interval_s: float = 2.0,
        vol_smoothing: float = 0.94,
        base_max_drawdown: float = 0.10,
        base_var_limit: float = 0.05,
        base_latency_ms: float = 500.0,
    ) -> None:
        self._bus = bus
        self._interval = interval_s
        self._vol_smoothing = vol_smoothing
        self._base_max_dd = base_max_drawdown
        self._base_var = base_var_limit
        self._base_latency = base_latency_ms

        self._ctx = CrossAssetContext()
        self._prev_price: Optional[float] = None
        self._mode = get_mode_manager()
        self._running = False
        self._task: Optional[asyncio.Task[None]] = None

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        # HIGH-FREQUENCY micro plane.
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("indicator.obi", self._on_obi)
        self._bus.subscribe("indicator.volume_delta", self._on_volume_delta)
        self._bus.subscribe("alt.funding_rate", self._on_funding)
        # LOW-FREQUENCY macro plane.
        self._bus.subscribe("core.macro.context", self._on_macro)
        # WORLD coupling (only consumed while in SIMULATION).
        self._bus.subscribe("world.micro_impact", self._on_world_impact)
        logger.info("core AI orchestrator registered (HF micro + LF macro planes)")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="core-ai-orchestrator")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- high-frequency ingest ------------------------------------------------
    async def _on_ticker(self, event: Event) -> None:
        price = event.payload.get("price")
        if price is None:
            return
        price = float(price)
        if self._prev_price and self._prev_price > 0:
            ret = abs(price / self._prev_price - 1.0)
            # EWMA of absolute returns as a cheap realised-vol proxy.
            self._ctx.realized_vol = (
                self._vol_smoothing * self._ctx.realized_vol
                + (1.0 - self._vol_smoothing) * ret
            )
        self._prev_price = price
        self._ctx.price = price
        self._touch()

    async def _on_obi(self, event: Event) -> None:
        self._ctx.obi = _as_float(event.payload.get("obi"))
        self._touch()

    async def _on_volume_delta(self, event: Event) -> None:
        self._ctx.volume_delta = _as_float(event.payload.get("volume_delta"))
        self._touch()

    async def _on_funding(self, event: Event) -> None:
        self._ctx.funding_rate = _as_float(event.payload.get("funding_rate"))
        self._touch()

    # -- low-frequency ingest -------------------------------------------------
    async def _on_macro(self, event: Event) -> None:
        macro = event.payload.get("macro", {}) or {}
        self._ctx.fed_funds_rate = _as_float(macro.get("fed_funds_effective_rate"))
        self._ctx.dollar_index = _as_float(macro.get("dollar_index_dxy"))
        self._ctx.yield_spread_10y_2y = _as_float(macro.get("yield_spread_10y_2y"))
        label = event.payload.get("regime_label")
        if label:
            self._ctx.macro_regime = str(label)
        self._touch()

    async def _on_world_impact(self, event: Event) -> None:
        # SOVEREIGNTY: world shock only informs the orchestrator in SIMULATION.
        if self._mode.mode is not QuantMode.SIMULATION:
            return
        mag = _as_float(event.payload.get("magnitude")) or 0.3
        self._ctx.world_shock = max(0.0, min(1.0, abs(mag)))
        self._touch()

    def _touch(self) -> None:
        self._ctx.updated_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # -- fusion + directive loop ----------------------------------------------
    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                await self._emit_directives()
            except Exception:  # noqa: BLE001 - a fusion fault must not kill the loop
                logger.exception("core AI directive emission failed")

    async def _emit_directives(self) -> None:
        quant = self._derive_quant()
        risk = self._derive_risk()
        await self._bus.publish("meta.quant.directive", **quant.payload())
        await self._bus.publish("meta.risk.directive", **risk.payload())
        # Publish a consolidated context snapshot for telemetry/dashboards.
        await self._bus.publish("meta.context", **self._ctx.snapshot())
        if self._mode.mode is QuantMode.SIMULATION:
            world = self._derive_world()
            await self._bus.publish("meta.world.directive", **world.payload())

    # -- derivation logic (context -> directives) -----------------------------
    def _derive_quant(self) -> QuantDirective:
        """Higher vol / inverted curve / dollar strength -> defensive appetite."""
        vol = self._ctx.realized_vol
        # Risk appetite decays as realised vol rises (turbulence -> shrink size).
        appetite = 1.0 / (1.0 + 40.0 * vol)
        # Macro lean: an inverted yield curve or a strong dollar is risk-off for
        # crypto; OBI adds a short-term microstructure tilt.
        bias = 0.0
        spread = self._ctx.yield_spread_10y_2y
        if spread is not None:
            bias += math.tanh(spread * 5.0)          # positive curve -> risk-on
        if self._ctx.obi is not None:
            bias += 0.5 * math.tanh(self._ctx.obi)
        bias = max(-1.0, min(1.0, bias))
        appetite = max(0.05, min(1.0, appetite))
        rationale = (
            f"vol={vol:.4f} regime={self._ctx.macro_regime} "
            f"spread={spread if spread is not None else 'na'}"
        )
        return QuantDirective(
            risk_appetite=appetite,
            directional_bias=bias,
            regime_hint=self._ctx.macro_regime,
            rationale=rationale,
        )

    def _derive_risk(self) -> RiskDirective:
        """Tighten Sentinel thresholds as turbulence + world shock rise."""
        vol = self._ctx.realized_vol
        stress = min(1.0, 30.0 * vol + 0.5 * self._ctx.world_shock)
        # Under stress, shrink the drawdown/VaR budget and demand lower latency.
        max_dd = self._base_max_dd * (1.0 - 0.6 * stress)
        var_limit = self._base_var * (1.0 - 0.5 * stress)
        latency = self._base_latency * (1.0 - 0.3 * stress)
        return RiskDirective(
            max_drawdown_pct=max(0.01, max_dd),
            var_limit_pct=max(0.01, var_limit),
            latency_limit_ms=max(150.0, latency),
            rationale=f"stress={stress:.3f} vol={vol:.4f} world_shock={self._ctx.world_shock:.3f}",
        )

    def _derive_world(self) -> WorldDirective:
        """Feed observed market turbulence back as world volatility pressure."""
        pressure = min(1.0, 25.0 * self._ctx.realized_vol + self._ctx.world_shock)
        return WorldDirective(
            volatility_pressure=pressure,
            macro_regime=self._ctx.macro_regime,
            rationale=f"observed_vol={self._ctx.realized_vol:.4f}",
        )

    # -- reads ----------------------------------------------------------------
    def context(self) -> dict[str, Any]:
        return self._ctx.snapshot()


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        f = float(value)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None
