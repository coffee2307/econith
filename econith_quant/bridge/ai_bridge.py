"""ECONITH Quant :: bridge.ai_bridge

Translates AI decisions into order intents under Sentinel veto
(master plan, Phase 4 + Phase 3 governance).

Subscribes to ``ai.signal`` (advisory) and ``sentinel.status`` (authoritative).
The Sentinel ALWAYS wins:

    * mode FROZEN       -> reject every intent (publish ai.veto).
    * mode REDUCE_ONLY  -> only allow reduce-only / flattening intents.
    * mode NORMAL       -> forward the intent to the exchange bridge.

Accepted intents are published on ``order.intent`` for the exchange bridge.
"""
from __future__ import annotations

import logging
from typing import Any

from ai.quant.portfolio import PortfolioRiskModel, PortfolioState
from config.environment import get_environment
from core.event_bus import Event, EventBus
from econith.quant.routing import EconithRouteKernel
from econith_quant.execution.smart_order import OrderIntent, OrderSide

logger = logging.getLogger("econith.quant.bridge.ai")

# Fallback marks when only BTC ticker is streamed (for min-notional sizing).
_DEFAULT_MARKS: dict[str, float] = {
    "BTCUSDT": 64_000.0,
    "ETHUSDT": 3_400.0,
    "SOLUSDT": 150.0,
    "AVAXUSDT": 35.0,
    "NEARUSDT": 5.0,
    "SUIUSDT": 3.5,
    "DOGEUSDT": 0.15,
    "SHIBUSDT": 0.000025,
    "PEPEUSDT": 0.00001,
}

# Binance USDT-M futures minimum contract sizes (testnet ≈ mainnet).
_MIN_CONTRACT_QTY: dict[str, float] = {
    "BTCUSDT": 0.001,
    "ETHUSDT": 0.001,
    "SOLUSDT": 0.01,
}


class AIBridge:
    def __init__(
        self,
        bus: EventBus,
        router: EconithRouteKernel | None = None,
    ) -> None:
        self._bus = bus
        env = get_environment()
        self._min_delta = env.ai_min_exposure_delta
        self._conf_floor = env.ai_confidence_floor
        self._base_notional = env.ai_base_notional_usd
        self._min_leg_notional = env.ai_min_leg_notional_usd
        self._demo_execution = env.is_demo_execution
        self._mode = "NORMAL"
        self._target_exposure = 0.0   # current desired exposure in [-1, 1]
        self._router = router or EconithRouteKernel()
        self._marks: dict[str, float] = dict(_DEFAULT_MARKS)
        self._risk = PortfolioRiskModel()
        self._derisk = 1.0

    def register(self) -> None:
        self._bus.subscribe("sentinel.status", self._on_sentinel)
        self._bus.subscribe("ai.signal", self._on_signal)
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("meta.quant.directive", self._on_quant_directive)
        logger.info("ai bridge registered (sentinel-gated + portfolio derisk)")

    async def _on_quant_directive(self, event: Event) -> None:
        """Core AI risk_appetite scales global size (portfolio path)."""
        appetite = float(event.payload.get("risk_appetite", 1.0) or 1.0)
        self._derisk = max(0.05, min(1.0, appetite))

    async def _on_ticker(self, event: Event) -> None:
        sym = str(event.payload.get("symbol", "")).upper()
        price = event.payload.get("price")
        if sym and price is not None:
            self._marks[sym] = float(price)

    def _mark(self, symbol: str) -> float:
        sym = symbol.upper()
        return self._marks.get(sym) or _DEFAULT_MARKS.get(sym) or 65_000.0

    def _min_leg_qty(self, symbol: str) -> float:
        mark = self._mark(symbol)
        notional_qty = self._min_leg_notional / mark
        exchange_min = _MIN_CONTRACT_QTY.get(symbol.upper(), 0.001)
        return max(notional_qty, exchange_min)

    async def _on_sentinel(self, event: Event) -> None:
        self._mode = event.payload.get("mode", "NORMAL")

    async def _on_signal(self, event: Event) -> None:
        p: dict[str, Any] = event.payload
        action = p.get("action", "FLAT")
        direction = float(p.get("direction", 0.0))
        confidence = float(p.get("confidence", 0.0))

        if action == "FLAT":
            target = 0.0
        else:
            sizing_conf = (
                max(confidence, self._conf_floor)
                if abs(direction) >= 0.02
                else confidence
            )
            target = direction * sizing_conf
        delta = target - self._target_exposure
        if abs(delta) < self._min_delta:
            return

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        is_reducing_exposure = (
            abs(target) < abs(self._target_exposure) - 1e-9 or action == "FLAT"
        )
        # Closing/flattening may use reduceOnly; opening never should (no position yet).
        reduce_only = bool(is_reducing_exposure)

        # --- Sentinel veto enforcement --------------------------------------
        if self._mode == "FROZEN":
            await self._veto("FROZEN", action)
            return
        if self._mode == "REDUCE_ONLY" and not is_reducing_exposure:
            await self._veto("REDUCE_ONLY", action)
            return

        self._target_exposure = target
        ref_mark = self._mark("BTCUSDT")
        # Portfolio VaR haircut from a single-desk proxy book (BTC exposure).
        state = PortfolioState(
            symbols=["BTCUSDT"],
            weights=[float(self._target_exposure)],
        )
        var_scalar = self._risk.derisk_scalar(state)
        size_scalar = max(0.05, min(1.0, self._derisk * var_scalar))
        base_qty = (self._base_notional / ref_mark) * abs(delta) * size_scalar        # Demo wallet is small — route BTC perp only until multi-asset sizing is tuned.
        route_symbol = "BTCUSDT" if self._demo_execution else (
            str(p.get("symbol", "")).upper() or None
        )
        plan = self._router.build_plan(
            direction=direction,
            confidence=confidence,
            base_quantity=round(base_qty, 8),
            reduce_only=reduce_only,
            symbol=route_symbol,
        )
        await self._bus.publish(
            "quant.route.plan",
            **plan.payload(),
            portfolio_derisk=round(size_scalar, 4),
            portfolio_var_scalar=round(var_scalar, 4),
        )
        for leg in plan.legs:
            qty = max(leg.quantity, self._min_leg_qty(leg.symbol))
            intent = OrderIntent(
                symbol=leg.symbol,
                side=OrderSide.BUY if leg.side == "BUY" else OrderSide.SELL,
                quantity=round(qty, 8),
                reason=(
                    f"ai {action} dir={direction:.2f} conf={confidence:.2f} "
                    f"regime={p.get('regime')} | {leg.reason}"
                ),
                reduce_only=reduce_only,
            )
            await self._bus.publish(
                "order.intent",
                symbol=intent.symbol,
                side=intent.side.value,
                quantity=intent.quantity,
                reduce_only=intent.reduce_only,
                reason=intent.reason,
            )

    def router_status(self) -> dict[str, Any]:
        return self._router.status()

    def set_router_profile(self, profile: str) -> dict[str, Any]:
        active = self._router.set_profile(profile)
        return active.payload()

    async def _veto(self, mode: str, action: str) -> None:
        await self._bus.publish(
            "ai.veto",
            mode=mode,
            action=action,
            message=f"Sentinel {mode}: vetoed AI {action}",
        )
        await self._bus.publish(
            "system.log",
            level="warn",
            source="ai_bridge",
            message=f"Sentinel {mode} -- vetoed AI {action}",
        )
