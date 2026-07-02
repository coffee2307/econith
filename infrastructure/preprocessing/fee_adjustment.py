"""ECONITH :: infrastructure.preprocessing.fee_adjustment

Transaction-cost amortisation (master plan, Phase 1, Step 4).

Every training / backtest dataset must price in real execution cost so the AI
cannot hallucinate edge. The cost-adjusted reference prices are:

    effective_buy  = close * (1 + slippage) * (1 + taker_fee)
    effective_sell = close * (1 - slippage) * (1 - taker_fee)

These are computed once on the Feature Store so all downstream consumers share a
single, conservative cost assumption.
"""
from __future__ import annotations

from dataclasses import dataclass

from infrastructure.preprocessing.slippage import apply_slippage

# Binance spot taker fee (fractional). Override per-venue as needed.
DEFAULT_TAKER_FEE = 0.0004  # 0.04%


@dataclass(slots=True, frozen=True)
class CostAdjustedPrices:
    close: float
    effective_buy: float
    effective_sell: float
    taker_fee: float
    slippage_bps: float


def cost_adjusted_prices(
    close: float,
    taker_fee: float = DEFAULT_TAKER_FEE,
    slippage_bps: float = 1.0,
    liquidity_factor: float = 1.0,
) -> CostAdjustedPrices:
    """Compute conservative buy/sell execution prices from a close price."""
    buy = apply_slippage(close, "buy", slippage_bps, liquidity_factor) * (1.0 + taker_fee)
    sell = apply_slippage(close, "sell", slippage_bps, liquidity_factor) * (1.0 - taker_fee)
    return CostAdjustedPrices(
        close=close,
        effective_buy=buy,
        effective_sell=sell,
        taker_fee=taker_fee,
        slippage_bps=slippage_bps,
    )
