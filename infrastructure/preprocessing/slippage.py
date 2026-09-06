"""ECONITH :: infrastructure.preprocessing.slippage

Assumed-slippage model (master plan, Phase 1, Step 4).

Slippage is expressed in basis points (1 bps = 0.01%). Buys execute *above* the
reference (close) price, sells *below* it. An optional liquidity factor widens
slippage as top-of-book depth thins, so backtests never assume free fills.
"""
from __future__ import annotations

from typing import Literal

Side = Literal["buy", "sell"]


def apply_slippage(
    price: float,
    side: Side,
    slippage_bps: float = 1.0,
    liquidity_factor: float = 1.0,
) -> float:
    """Return the slippage-adjusted execution price.

    ``liquidity_factor`` >= 1 amplifies slippage in thin books.
    """
    frac = (slippage_bps / 10_000.0) * max(liquidity_factor, 0.0)
    if side == "buy":
        return price * (1.0 + frac)
    return price * (1.0 - frac)
