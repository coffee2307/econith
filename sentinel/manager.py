"""ECONITH :: sentinel.manager

The Sentinel -- the platform's independent "master circuit breaker"
(master plan, Phase 3). It runs fully decoupled from the AI and holds veto
power: when the safety mathematics are violated it freezes trading regardless
of what any strategy/agent wants.

P0 REFACTOR (Equity Synchronization)
------------------------------------
Historically the Sentinel tracked a *mock* equity curve derived purely from the
market price feed on top of a static $1,000,000 ledger. That decoupled its risk
mathematics (VaR / drawdown) from the actual capital the engine deployed via the
``quant.fill`` execution stream, so it governed a *ghost budget*.

The Sentinel now anchors its principal equity base to **execution truth**:

  * It subscribes to ``quant.fill`` and replays every matched execution through
    the exact same position/PnL accounting used by :class:`CockpitTelemetryHub`
    (starting capital + realised PnL + unrealised mark-to-market). This makes its
    equity match the Cockpit Fuel Gauge 1:1.
  * It retains its ``md.ticker`` subscription strictly for *mark-to-market*
    revaluation of open positions (unrealised PnL) and as a latency heartbeat --
    it no longer fabricates equity from raw price moves.

Governance thresholds (from the master plan):
  * 24h VaR > 3% of capital            -> soft freeze: REDUCE_ONLY.
  * Account drawdown > 3%              -> hard freeze: FROZEN (reject new orders).
  * Latency > 300ms, 3x consecutive    -> hard freeze via the circuit breaker.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional

from core.event_bus import Event, EventBus
from sentinel.circuit_breaker import BreakerState, CircuitBreaker
from sentinel.var import HistoricalSimulationVaR

logger = logging.getLogger("econith.sentinel.manager")

# Shared with :class:`CockpitTelemetryHub` so both read-models start from an
# identical principal base and therefore agree on equity to the cent.
DEFAULT_STARTING_CAPITAL: float = 100_000.0


class SystemMode(str, Enum):
    NORMAL = "NORMAL"
    REDUCE_ONLY = "REDUCE_ONLY"
    FROZEN = "FROZEN"


@dataclass(slots=True)
class SentinelStatus:
    state: str            # circuit-breaker state
    mode: str             # resolved system mode
    equity: float         # execution-truth equity (capital + realised + unrealised)
    peak_equity: float
    drawdown: float       # fractional, e.g. 0.031 == 3.1%
    var: float
    cvar: float
    var_method: str
    latency_ms: float
    last_price: float
    breaker_reason: str


class Sentinel:
    def __init__(
        self,
        bus: EventBus,
        starting_capital: float = DEFAULT_STARTING_CAPITAL,
        max_drawdown_pct: float = 0.03,
        var_limit_pct: float = 0.03,
        latency_limit_ms: float = 300.0,
        freeze_cooldown_s: float = 15.0,
        status_interval_s: float = 1.0,
    ) -> None:
        self._bus = bus
        self._starting_capital = starting_capital
        self._max_dd = max_drawdown_pct
        self._var_limit = var_limit_pct
        self._latency_limit = latency_limit_ms
        self._cooldown = freeze_cooldown_s
        self._status_interval = status_interval_s

        # Operator-configured baselines. The Core AI orchestrator may nudge the
        # live thresholds within a bounded envelope around these; the baselines
        # are the floor/ceiling so a bad directive can never fully disarm risk.
        self._base_max_dd = max_drawdown_pct
        self._base_var_limit = var_limit_pct
        self._base_latency = latency_limit_ms

        self._risk = HistoricalSimulationVaR()
        self._breaker = CircuitBreaker(
            name="sentinel",
            failure_threshold=3,
            reset_timeout_s=freeze_cooldown_s,
            on_transition=self._on_breaker_transition,
        )

        # --- execution-truth ledger (mirrors CockpitTelemetryHub 1:1) --------
        self._positions: dict[str, dict[str, float]] = {}
        self._marks: dict[str, float] = {}
        self._realized_total: float = 0.0
        self._unrealized: float = 0.0
        self._equity: float = starting_capital
        self._prev_equity: float = starting_capital
        self._peak_equity: float = starting_capital

        # --- price / latency heartbeat state ---------------------------------
        self._last_price: float = 0.0

        # risk / latency state
        self._latency_ms: float = 0.0
        self._var_soft_breach = False
        self._risk_frozen = False
        self._risk_unfreeze_at: float = 0.0

        self._running = False
        self._task: Optional[asyncio.Task[None]] = None

    # -- lifecycle ------------------------------------------------------------
    def register(self) -> None:
        # Execution truth: the principal equity base is driven by real fills.
        self._bus.subscribe("quant.fill", self._on_fill)
        # Mark-to-market + latency heartbeat only (never fabricates equity).
        self._bus.subscribe("md.ticker", self._on_ticker)
        # Core AI dynamic threshold recalibration (advisory, bounded).
        self._bus.subscribe("meta.risk.directive", self._on_risk_directive)
        logger.info(
            "sentinel registered to execution (quant.fill) + market feed + meta directives"
        )

    async def _on_risk_directive(self, event: Event) -> None:
        """Apply a Core AI risk directive, clamped to a safe envelope.

        The orchestrator can only *tighten* toward the baseline or loosen up to a
        bounded ceiling; it can never disable a limit. This keeps the Sentinel
        the ultimate authority while letting the meta-brain adapt to volatility.
        """
        p = event.payload
        self._max_dd = self._clamp_threshold(
            p.get("max_drawdown_pct"), self._base_max_dd, lo_factor=0.3, hi_factor=1.0
        )
        self._var_limit = self._clamp_threshold(
            p.get("var_limit_pct"), self._base_var_limit, lo_factor=0.3, hi_factor=1.0
        )
        self._latency_limit = self._clamp_threshold(
            p.get("latency_limit_ms"), self._base_latency, lo_factor=0.5, hi_factor=1.5
        )

    @staticmethod
    def _clamp_threshold(
        value: object, baseline: float, *, lo_factor: float, hi_factor: float
    ) -> float:
        try:
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return baseline
        if not (v == v) or v in (float("inf"), float("-inf")):  # NaN / Inf guard
            return baseline
        lo, hi = baseline * lo_factor, baseline * hi_factor
        return max(lo, min(hi, v))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._log(
            "Sentinel armed -- monitoring execution equity, drawdown, VaR and latency"
        )
        self._task = asyncio.create_task(self.run(), name="sentinel")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- external controls (FastAPI) -----------------------------------------
    def reset(self) -> None:
        """Operator re-arm: re-baseline equity peak and clear risk freeze."""
        self._peak_equity = self._equity
        self._risk_frozen = False
        self._var_soft_breach = False

    # -- event handlers -------------------------------------------------------
    async def _on_fill(self, event: Event) -> None:
        """Replay a matched execution into the execution-truth ledger.

        Uses the exact position/PnL algorithm of :class:`CockpitTelemetryHub`
        so Sentinel equity and Cockpit Fuel Gauge equity are identical.
        """
        p = event.payload
        try:
            asset = str(p["asset"]).upper()
            side = str(p["side"])
            qty = float(p["filledVolume"])
            price = float(p["fillPrice"])
            commission = float(p.get("commission", 0.0))
        except (KeyError, TypeError, ValueError):
            logger.exception("sentinel received malformed quant.fill payload")
            return

        pos = self._positions.setdefault(asset, {"qty": 0.0, "avg": 0.0})
        signed = qty * (1.0 if side.startswith("LONG") else -1.0)
        is_close = side.endswith("CLOSE")
        if is_close and pos["qty"] != 0.0:
            direction = 1.0 if pos["qty"] > 0 else -1.0
            realized = direction * (price - pos["avg"]) * qty - commission
            self._realized_total += realized
            pos["qty"] -= direction * qty
        else:
            new_qty = pos["qty"] + signed
            if new_qty != 0.0:
                pos["avg"] = (
                    pos["avg"] * abs(pos["qty"]) + price * abs(signed)
                ) / abs(new_qty)
            pos["qty"] = new_qty

        self._marks[asset] = price
        await self._revalue_and_govern()

    async def _on_ticker(self, event: Event) -> None:
        """Mark-to-market open positions + latency heartbeat (no equity fabrication)."""
        price = float(event.payload["price"])
        self._last_price = price

        # Re-mark the traded symbol so unrealised PnL tracks live price.
        symbol = str(event.payload.get("symbol", "")).upper()
        if symbol and symbol in self._positions:
            self._marks[symbol] = price

        # latency / heartbeat proxy
        event_ms = int(event.payload.get("event_ms", 0))
        if event_ms:
            self._latency_ms = max(0.0, time.time() * 1000 - event_ms)
            if self._latency_ms > self._latency_limit:
                await self._breaker.record_failure(
                    f"latency {self._latency_ms:.0f}ms > {self._latency_limit:.0f}ms"
                )
            else:
                await self._breaker.record_success()

        await self._revalue_and_govern()

    # -- valuation + risk governance -----------------------------------------
    async def _revalue_and_govern(self) -> None:
        """Recompute execution-truth equity, feed portfolio VaR, enforce drawdown."""
        self._recompute_unrealized()
        equity = self._starting_capital + self._realized_total + self._unrealized

        # Feed *portfolio* returns (not raw price returns) into the VaR engine.
        if self._prev_equity > 0 and equity != self._prev_equity:
            self._risk.update((equity - self._prev_equity) / self._prev_equity)
        self._prev_equity = equity
        self._equity = equity
        self._peak_equity = max(self._peak_equity, equity)

        drawdown = self._drawdown()
        if drawdown >= self._max_dd and not self._risk_frozen:
            self._risk_frozen = True
            self._risk_unfreeze_at = time.monotonic() + self._cooldown
            await self._emergency(
                action="FREEZE",
                reason=f"drawdown {drawdown*100:.2f}% breached {self._max_dd*100:.0f}% limit",
            )

    def _recompute_unrealized(self) -> None:
        total = 0.0
        for sym, pos in self._positions.items():
            if pos["qty"] == 0.0:
                continue
            mark = self._marks.get(sym, pos["avg"])
            total += (mark - pos["avg"]) * pos["qty"]
        self._unrealized = total

    # -- active risk loop -----------------------------------------------------
    async def run(self) -> None:
        while self._running:
            await asyncio.sleep(self._status_interval)

            # recompute VaR / C-VaR each cycle
            est = self._risk.estimate()
            prev_soft = self._var_soft_breach
            self._var_soft_breach = est.var > self._var_limit
            if self._var_soft_breach and not prev_soft:
                await self._emergency(
                    action="REDUCE_ONLY",
                    reason=f"24h VaR {est.var*100:.2f}% > {self._var_limit*100:.0f}% limit",
                )

            # auto re-arm after the hard-freeze cooldown
            if self._risk_frozen and time.monotonic() >= self._risk_unfreeze_at:
                self._peak_equity = self._equity   # re-baseline
                self._risk_frozen = False
                await self._log("drawdown cooldown elapsed -- Sentinel re-armed", "ok")

            await self._publish_status()

    # -- helpers --------------------------------------------------------------
    def _drawdown(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return max(0.0, (self._peak_equity - self._equity) / self._peak_equity)

    def _resolve_mode(self) -> SystemMode:
        if self._breaker.is_open or self._risk_frozen:
            return SystemMode.FROZEN
        if self._var_soft_breach or self._breaker.state is BreakerState.HALF_OPEN:
            return SystemMode.REDUCE_ONLY
        return SystemMode.NORMAL

    def status(self) -> SentinelStatus:
        est = self._risk.estimate()
        return SentinelStatus(
            state=self._breaker.state.value,
            mode=self._resolve_mode().value,
            equity=round(self._equity, 2),
            peak_equity=round(self._peak_equity, 2),
            drawdown=round(self._drawdown(), 5),
            var=round(est.var, 5),
            cvar=round(est.cvar, 5),
            var_method=est.method,
            latency_ms=round(self._latency_ms, 1),
            last_price=round(self._last_price, 2),
            breaker_reason=self._breaker.last_reason,
        )

    async def _publish_status(self) -> None:
        await self._bus.publish("sentinel.status", **asdict(self.status()))

    async def _emergency(self, action: str, reason: str) -> None:
        # A hard FREEZE trips the breaker; its transition hook publishes the
        # single canonical emergency event. Soft actions publish directly.
        if action == "FREEZE":
            await self._breaker.trip(reason)
        else:
            await self._bus.publish(
                "sentinel.emergency",
                action=action,
                reason=reason,
                mode=self._resolve_mode().value,
            )

    async def _on_breaker_transition(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        if new is BreakerState.OPEN:
            await self._bus.publish(
                "sentinel.emergency", action="FREEZE", reason=reason, mode="FROZEN"
            )
            await self._log(
                f"circuit breaker {old.value} -> {new.value}: {reason}", "danger"
            )
        else:
            await self._log(
                f"circuit breaker {old.value} -> {new.value}: {reason}", "ok"
            )

    async def _log(self, message: str, level: str = "info") -> None:
        await self._bus.publish(
            "system.log", level=level, source="sentinel", message=message
        )


# Explicit alias so the module's async handler contract is importable/testable.
FillHandler = Callable[[Event], Awaitable[None]]
