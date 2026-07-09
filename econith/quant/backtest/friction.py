"""ECONITH :: econith.quant.backtest.friction

Native analytical transaction-cost / market-impact / fee curves — internalizes
the Zipline-Reloaded friction idea (volume-share slippage + per-notional
commission + half-spread) as pure math, with no external package.

Compatibility interface:  friction_quote(order, market) -> cost
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["FrictionQuote", "EconithFrictionModel"]


@dataclass(slots=True)
class FrictionQuote:
    fill_price: float
    commission: float
    slippage_bps: float
    total_cost: float

    def payload(self) -> dict[str, Any]:
        return {
            "fill_price": round(self.fill_price, 8),
            "commission": round(self.commission, 8),
            "slippage_bps": round(self.slippage_bps, 4),
            "total_cost": round(self.total_cost, 8),
        }


@dataclass(slots=True)
class EconithFrictionModel:
    """Analytical friction model for the backtester and cost estimation."""

    fee_bps: float = 4.0            # taker fee per side
    slippage_bps: float = 1.0       # base market-impact slippage
    spread_bps: float = 2.0         # half-spread crossing the book
    impact_coeff: float = 0.5       # volume-share impact multiplier

    def aggregate_friction_bps(self) -> float:
        """Round-trip-normalised static friction in bps (per unit turnover)."""
        return self.fee_bps + self.slippage_bps + self.spread_bps

    def _volume_share_slippage_bps(self, quantity: float, adv: float) -> float:
        """Square-root market-impact: slippage grows with participation rate."""
        if adv <= 0 or quantity <= 0:
            return self.slippage_bps
        participation = min(1.0, quantity / adv)
        return self.slippage_bps + self.impact_coeff * (participation ** 0.5) * 1e4 * 0.0001

    def friction_quote(self, order: dict[str, Any], market: dict[str, Any]) -> FrictionQuote:
        """friction_quote(order, market) -> cost.

        ``order``  : {price, quantity, side}
        ``market`` : {adv?}  (average daily volume for impact scaling)
        """
        price = float(order.get("price", 0.0))
        quantity = float(order.get("quantity", 0.0))
        side = str(order.get("side", "BUY")).upper()
        adv = float(market.get("adv", 0.0))

        slip_bps = self._volume_share_slippage_bps(abs(quantity), adv)
        half_bps = (self.spread_bps + slip_bps) / 1e4
        signed = half_bps * price * (1.0 if side == "BUY" else -1.0)
        fill_price = price + signed
        commission = abs(quantity) * price * (self.fee_bps / 1e4)
        impact_cost = abs(quantity) * abs(signed)
        return FrictionQuote(
            fill_price=fill_price,
            commission=commission,
            slippage_bps=slip_bps,
            total_cost=commission + impact_cost,
        )
