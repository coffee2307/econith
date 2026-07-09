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

from core.event_bus import Event, EventBus
from econith.quant.routing import EconithRouteKernel
from econith_quant.execution.smart_order import OrderIntent, OrderSide

logger = logging.getLogger("econith.quant.bridge.ai")


class AIBridge:
    def __init__(
        self,
        bus: EventBus,
        base_quantity: float = 1.0,
        router: EconithRouteKernel | None = None,
    ) -> None:
        self._bus = bus
        self._base_qty = base_quantity
        self._mode = "NORMAL"
        self._target_exposure = 0.0   # current desired exposure in [-1, 1]
        self._router = router or EconithRouteKernel()

    def register(self) -> None:
        self._bus.subscribe("sentinel.status", self._on_sentinel)
        self._bus.subscribe("ai.signal", self._on_signal)
        logger.info("ai bridge registered (sentinel-gated)")

    async def _on_sentinel(self, event: Event) -> None:
        self._mode = event.payload.get("mode", "NORMAL")

    async def _on_signal(self, event: Event) -> None:
        p: dict[str, Any] = event.payload
        action = p.get("action", "FLAT")
        direction = float(p.get("direction", 0.0))
        confidence = float(p.get("confidence", 0.0))

        target = direction * confidence            # desired exposure
        delta = target - self._target_exposure     # change required
        if abs(delta) < 0.03:
            return  # negligible adjustment; avoid churn

        side = OrderSide.BUY if delta > 0 else OrderSide.SELL
        reduce_only = abs(target) < abs(self._target_exposure)

        # --- Sentinel veto enforcement --------------------------------------
        if self._mode == "FROZEN":
            await self._veto("FROZEN", action)
            return
        if self._mode == "REDUCE_ONLY" and not reduce_only:
            await self._veto("REDUCE_ONLY", action)
            return

        self._target_exposure = target
        base_qty = round(abs(delta) * self._base_qty, 8)
        plan = self._router.build_plan(
            direction=direction,
            confidence=confidence,
            base_quantity=base_qty,
            reduce_only=reduce_only,
            symbol=str(p.get("symbol", "")).upper() or None,
        )
        await self._bus.publish("quant.route.plan", **plan.payload())
        for leg in plan.legs:
            intent = OrderIntent(
                symbol=leg.symbol,
                side=OrderSide.BUY if leg.side == "BUY" else OrderSide.SELL,
                quantity=round(leg.quantity, 8),
                reason=f"ai {action} dir={direction:.2f} conf={confidence:.2f} regime={p.get('regime')} | {leg.reason}",
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
