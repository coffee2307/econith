"""ECONITH Quant :: execution.twap

Time-Weighted Average Price slicer (master plan, Phase 4, Step 1).

Splits an order intent into ``slices`` equal child orders spread evenly over a
horizon, each placed as a passive (post-only) maker limit. This minimises market
impact versus a single aggressive fill.
"""
from __future__ import annotations

from econith_quant.execution.smart_order import (
    ChildOrder,
    OrderIntent,
    TimeInForce,
)


class TWAPExecutor:
    def __init__(self, slices: int = 10, interval_s: float = 1.0) -> None:
        self._slices = max(1, slices)
        self._interval_s = interval_s

    @property
    def interval_s(self) -> float:
        return self._interval_s

    def plan(self, intent: OrderIntent, reference_price: float) -> list[ChildOrder]:
        """Build an even child-order schedule for ``intent``."""
        per_slice = intent.quantity / self._slices
        # Passive maker: buys rest just below ref, sells just above ref.
        offset = reference_price * 0.0001  # 1 bps inside the spread
        limit = (
            reference_price - offset
            if intent.side.value == "BUY"
            else reference_price + offset
        )
        return [
            ChildOrder(
                symbol=intent.symbol,
                side=intent.side,
                quantity=round(per_slice, 8),
                limit_price=round(limit, 2),
                tif=TimeInForce.POST_ONLY,
                slice_index=i,
                slice_total=self._slices,
                algo="twap",
            )
            for i in range(self._slices)
        ]
