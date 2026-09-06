"""ECONITH Quant :: execution.vwap

Volume-Weighted Average Price slicer (master plan, Phase 4, Step 1).

Like TWAP, but child-order sizes follow a historical intraday liquidity profile
(more volume placed during liquid periods), reducing impact further. The profile
is injectable; a flat profile degrades gracefully to TWAP behaviour.
"""
from __future__ import annotations

from econith_quant.execution.smart_order import (
    ChildOrder,
    OrderIntent,
    TimeInForce,
)

# A coarse normalised intraday liquidity profile (sums to 1.0). Replace with a
# venue-calibrated curve from the Feature Store in later phases.
DEFAULT_PROFILE = (0.12, 0.10, 0.08, 0.08, 0.10, 0.12, 0.14, 0.10, 0.08, 0.08)


class VWAPExecutor:
    def __init__(self, profile: tuple[float, ...] = DEFAULT_PROFILE) -> None:
        total = sum(profile) or 1.0
        self._profile = tuple(p / total for p in profile)

    @property
    def slices(self) -> int:
        return len(self._profile)

    def plan(self, intent: OrderIntent, reference_price: float) -> list[ChildOrder]:
        offset = reference_price * 0.0001
        limit = (
            reference_price - offset
            if intent.side.value == "BUY"
            else reference_price + offset
        )
        orders: list[ChildOrder] = []
        for i, weight in enumerate(self._profile):
            orders.append(
                ChildOrder(
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=round(intent.quantity * weight, 8),
                    limit_price=round(limit, 2),
                    tif=TimeInForce.POST_ONLY,
                    slice_index=i,
                    slice_total=len(self._profile),
                    algo="vwap",
                )
            )
        return orders
