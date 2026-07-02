"""ECONITH :: sentinel.manager

The Sentinel -- the platform's independent "master circuit breaker"
(master plan, Phase 3). It runs fully decoupled from the AI and holds veto
power: when the safety mathematics are violated it freezes trading regardless
of what any strategy/agent wants.

Responsibilities wired here (Phase 3 core):
  * Track a mock equity curve derived from the live (mock) price feed and
    compute real-time drawdown from the running peak.
  * Maintain a rolling VaR / C-VaR estimate (HistoricalSimulationVaR).
  * Monitor data latency as a heartbeat proxy (>300ms == fault).
  * Drive a CircuitBreaker and publish governance state + emergency events
    onto the EventBus.

Governance thresholds (from the master plan):
  * 24h VaR > 3% of capital            -> soft freeze: REDUCE_ONLY.
  * Account drawdown > 3%              -> hard freeze: FROZEN (reject new orders).
  * Latency > 300ms, 3x consecutive    -> hard freeze via the circuit breaker.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from enum import Enum

from core.event_bus import Event, EventBus
from sentinel.circuit_breaker import BreakerState, CircuitBreaker
from sentinel.var import HistoricalSimulationVaR

logger = logging.getLogger("econith.sentinel.manager")


class SystemMode(str, Enum):
    NORMAL = "NORMAL"
    REDUCE_ONLY = "REDUCE_ONLY"
    FROZEN = "FROZEN"


@dataclass(slots=True)
class SentinelStatus:
    state: str            # circuit-breaker state
    mode: str             # resolved system mode
    equity: float
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
        initial_equity: float = 1_000_000.0,
        exposure: float = 1.0,
        max_drawdown_pct: float = 0.03,
        var_limit_pct: float = 0.03,
        latency_limit_ms: float = 300.0,
        freeze_cooldown_s: float = 15.0,
        status_interval_s: float = 1.0,
    ) -> None:
        self._bus = bus
        self._initial_equity = initial_equity
        self._exposure = exposure
        self._max_dd = max_drawdown_pct
        self._var_limit = var_limit_pct
        self._latency_limit = latency_limit_ms
        self._cooldown = freeze_cooldown_s
        self._status_interval = status_interval_s

        self._risk = HistoricalSimulationVaR()
        self._breaker = CircuitBreaker(
            name="sentinel",
            failure_threshold=3,
            reset_timeout_s=freeze_cooldown_s,
            on_transition=self._on_breaker_transition,
        )

        # equity / price state
        self._open_price: float | None = None
        self._last_price: float = 0.0
        self._prev_price: float | None = None
        self._equity: float = initial_equity
        self._peak_equity: float = initial_equity

        # risk / latency state
        self._latency_ms: float = 0.0
        self._var_soft_breach = False
        self._risk_frozen = False
        self._risk_unfreeze_at: float = 0.0

        self._running = False
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle ------------------------------------------------------------
    def register(self) -> None:
        self._bus.subscribe("md.ticker", self._on_ticker)
        logger.info("sentinel registered to market feed")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._log("Sentinel armed -- monitoring drawdown, VaR and latency")
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
    async def _on_ticker(self, event: Event) -> None:
        price = float(event.payload["price"])
        self._last_price = price
        if self._open_price is None:
            self._open_price = price

        # realised per-tick return -> feeds the VaR engine
        if self._prev_price and self._prev_price > 0:
            self._risk.update((price / self._prev_price) - 1.0)
        self._prev_price = price

        # mock equity from price move vs the session open
        move = (price / self._open_price) - 1.0
        self._equity = self._initial_equity * (1.0 + self._exposure * move)
        self._peak_equity = max(self._peak_equity, self._equity)

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

        # hard drawdown freeze
        drawdown = self._drawdown()
        if drawdown >= self._max_dd and not self._risk_frozen:
            self._risk_frozen = True
            self._risk_unfreeze_at = time.monotonic() + self._cooldown
            await self._emergency(
                action="FREEZE",
                reason=f"drawdown {drawdown*100:.2f}% breached {self._max_dd*100:.0f}% limit",
            )

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
