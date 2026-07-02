"""ECONITH :: infrastructure.feature_store.builder

Assembles a flat feature row from a consolidated market snapshot
(master plan, Phase 1, Step 4).

This is intentionally framework-light (plain dicts) so it has zero heavy
dependencies on the hot path. The Feature Store writer batches these rows to
Parquet. The cost-adjusted reference prices are computed here so every feature
row already amortises execution cost.
"""
from __future__ import annotations

from typing import Any

from infrastructure.preprocessing.fee_adjustment import cost_adjusted_prices


class FeatureBuilder:
    """Builds cost-aware feature rows from market + alt-data fields."""

    def __init__(self, taker_fee: float = 0.0004, slippage_bps: float = 1.0) -> None:
        self._taker_fee = taker_fee
        self._slippage_bps = slippage_bps

    def build(self, market: dict[str, Any], alt: dict[str, Any] | None = None) -> dict[str, Any]:
        alt = alt or {}
        price = market.get("price") or market.get("mid") or 0.0
        row: dict[str, Any] = {
            "symbol": market.get("symbol"),
            "price": price,
            "mid": market.get("mid"),
            "best_bid": market.get("best_bid"),
            "best_ask": market.get("best_ask"),
            "obi": market.get("obi"),
            "bid_volume": market.get("bid_volume"),
            "ask_volume": market.get("ask_volume"),
            "volume_delta": market.get("volume_delta"),
            "buy_volume": market.get("buy_volume"),
            "sell_volume": market.get("sell_volume"),
            "trade_count": market.get("trade_count"),
            # alternative data
            "funding_rate": alt.get("funding_rate"),
            "time_to_funding_s": alt.get("time_to_funding_s"),
            "open_interest": alt.get("open_interest"),
            "oi_change_pct": alt.get("oi_change_pct"),
            "liquidation_notional": alt.get("total_notional"),
        }
        if price:
            costs = cost_adjusted_prices(
                price, taker_fee=self._taker_fee, slippage_bps=self._slippage_bps
            )
            row["effective_buy"] = costs.effective_buy
            row["effective_sell"] = costs.effective_sell
        return row
