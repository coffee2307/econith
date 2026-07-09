"""ECONITH :: econith.world.abides_kernel

Native synthetic LOB step kernel inspired by ABIDES-style microstructure.

This is intentionally constrained to ECONITH's event-driven runtime contract:
- no standalone simulation loop
- no blocking scheduler
- one deterministic submit() call per order intent
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.event_bus import Event, EventBus
from econith.base import BaseKernel

__all__ = ["AbidesFill", "AbidesStepKernel"]


@dataclass(slots=True)
class AbidesFill:
    symbol: str
    side: str
    filled_volume: float
    fill_price: float
    client_order_id: str
    mode: str = "SIMULATION"
    engine: str = "econith_abides"

    def payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "filledVolume": self.filled_volume,
            "fillPrice": self.fill_price,
            "mode": self.mode,
            "clientOrderId": self.client_order_id,
            "engine": self.engine,
        }


class AbidesStepKernel(BaseKernel):
    """Synchronous synthetic LOB kernel for SIMULATION order fills."""

    def __init__(self) -> None:
        super().__init__(name="econith.world.abides", simulation_only=True)
        self._bus: EventBus | None = None
        self._marks: dict[str, float] = {}

    def bind(self, bus: EventBus) -> None:
        """Attach bus consumers for live tape context."""
        self._bus = bus
        bus.subscribe("md.depth", self._on_depth)
        bus.subscribe("md.aggTrade", self._on_trade)

    async def _on_depth(self, event: Event) -> None:
        sym = event.payload.get("symbol")
        mid = event.payload.get("mid") or event.payload.get("price")
        if sym and mid is not None:
            self._marks[str(sym).upper()] = float(mid)

    async def _on_trade(self, event: Event) -> None:
        sym = event.payload.get("symbol")
        price = event.payload.get("price")
        if sym and price is not None:
            self._marks[str(sym).upper()] = float(price)

    async def submit(
        self, *, symbol: str, side: str, quantity: float, client_order_id: str = ""
    ) -> AbidesFill:
        self.ensure_mode()
        symbol_u = symbol.upper()
        mark = float(self._marks.get(symbol_u, 0.0))
        # Deterministic micro-impact proxy.
        slip_frac = 0.0005 * (1.0 if side.upper() == "BUY" else -1.0)
        fill_price = mark * (1.0 + slip_frac) if mark > 0 else 0.0
        fill = AbidesFill(
            symbol=symbol_u,
            side=side,
            filled_volume=quantity,
            fill_price=round(fill_price, 8),
            client_order_id=client_order_id,
        )
        if self._bus is not None:
            await self._bus.publish("quant.fill", **fill.payload())
        return fill

