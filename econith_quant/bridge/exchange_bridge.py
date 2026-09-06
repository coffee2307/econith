"""ECONITH Quant :: bridge.exchange_bridge

Routes accepted order intents to the venue via the stealth execution layer
(master plan, Phase 4, Steps 1-3).

Subscribes to ``order.intent``, slices it with the TWAP executor into passive
maker child orders, and (in mock mode) simulates working/filled lifecycle
transitions, persisting each to the recovery ledger. Every transition is also
published on ``order.update`` for the dashboard.

Mock TWAP is **opt-in** via ``ECONITH_MOCK_TWAP=true``. When CCXT execution is
the live path (default), this bridge stays quiet so cockpit does not show a
fake TWAP lifecycle beside real fills.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from core.event_bus import Event, EventBus
from econith_quant.execution.smart_order import OrderIntent, OrderSide
from econith_quant.execution.twap import TWAPExecutor
from econith_quant.recovery.state import TradeStateStore

logger = logging.getLogger("econith.quant.bridge.exchange")


def mock_twap_enabled() -> bool:
    return os.getenv("ECONITH_MOCK_TWAP", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class ExchangeBridge:
    def __init__(
        self,
        bus: EventBus,
        executor: TWAPExecutor | None = None,
        state: TradeStateStore | None = None,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._bus = bus
        self._executor = executor or TWAPExecutor(slices=5)
        self._state = state or TradeStateStore()
        self._last_price: float = 60_000.0
        self._enabled = mock_twap_enabled() if enabled is None else bool(enabled)

    def register(self) -> None:
        if not self._enabled:
            logger.info(
                "exchange bridge mock TWAP disabled "
                "(set ECONITH_MOCK_TWAP=true to enable legacy UI lifecycle)"
            )
            return
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("order.intent", self._on_intent)
        logger.info("exchange bridge registered (mock TWAP enabled)")

    async def _on_ticker(self, event: Event) -> None:
        self._last_price = float(event.payload.get("price", self._last_price))

    async def _on_intent(self, event: Event) -> None:
        if not self._enabled:
            return
        p: dict[str, Any] = event.payload
        intent = OrderIntent(
            symbol=p.get("symbol", "BTCUSDT"),
            side=OrderSide(p.get("side", "BUY")),
            quantity=float(p.get("quantity", 0.0)),
            reason=p.get("reason", ""),
            reduce_only=bool(p.get("reduce_only", False)),
        )
        if intent.quantity <= 0:
            return

        children = self._executor.plan(intent, self._last_price)
        await self._bus.publish(
            "order.update",
            symbol=intent.symbol,
            side=intent.side.value,
            status="SUBMITTED",
            slices=len(children),
            algo="twap",
            reason=intent.reason,
        )
        await self._bus.publish(
            "system.log",
            level="info",
            source="exchange_bridge",
            message=(
                f"TWAP {intent.side.value} {intent.quantity:.4f} {intent.symbol} "
                f"in {len(children)} maker slices"
            ),
        )

        # Mock fill: record each child as worked then filled at its limit.
        for child in children:
            self._state.record(
                symbol=child.symbol,
                side=child.side.value,
                quantity=child.quantity,
                limit_price=child.limit_price,
                algo=child.algo,
                status="FILLED",
                reason=intent.reason,
            )
        await self._bus.publish(
            "order.update",
            symbol=intent.symbol,
            side=intent.side.value,
            status="FILLED",
            slices=len(children),
            algo="twap",
            reason=intent.reason,
        )
