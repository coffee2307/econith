"""ECONITH Quant :: execution.chaser

Chaser / shadow-order logic (master plan, Phase 4, Step 2).

Keeps a resting maker order glued to the best bid/ask. If the market moves away
by more than a tolerance, the order is repriced to the new best quote so it stays
at the front of the queue while always remaining a liquidity *provider* (maker),
never crossing the spread.
"""
from __future__ import annotations

from dataclasses import dataclass

from econith_quant.execution.smart_order import ChildOrder, OrderSide, TimeInForce


@dataclass(slots=True)
class ChaserDecision:
    reprice: bool
    new_price: float
    reason: str


class ChaserExecutor:
    def __init__(self, tolerance_bps: float = 2.0) -> None:
        self._tolerance = tolerance_bps / 10_000.0

    def evaluate(
        self,
        order: ChildOrder,
        best_bid: float,
        best_ask: float,
    ) -> ChaserDecision:
        """Decide whether to reprice the resting order to track the touch."""
        target = best_bid if order.side is OrderSide.BUY else best_ask
        if target <= 0 or order.limit_price <= 0:
            return ChaserDecision(False, order.limit_price, "no quote")

        drift = abs(target - order.limit_price) / order.limit_price
        if drift > self._tolerance:
            return ChaserDecision(
                reprice=True,
                new_price=round(target, 2),
                reason=f"drift {drift*10_000:.1f}bps > {self._tolerance*10_000:.0f}bps",
            )
        return ChaserDecision(False, order.limit_price, "within tolerance")

    @staticmethod
    def is_maker(order: ChildOrder, best_bid: float, best_ask: float) -> bool:
        """Confirm the order would post as a maker (not cross the spread)."""
        if order.tif is not TimeInForce.POST_ONLY:
            return True
        if order.side is OrderSide.BUY:
            return order.limit_price < best_ask
        return order.limit_price > best_bid
