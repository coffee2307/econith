"""ECONITH Quant :: execution.smart_order

Shared order primitives for the stealth execution layer (master plan, Phase 4).

These dataclasses are the lingua franca between the AI bridge (which produces
``OrderIntent``s) and the execution algorithms (TWAP/VWAP/Chaser, which slice
intents into ``ChildOrder``s). Market orders are intentionally NOT representable
here -- the master plan forbids direct market orders; everything executes as a
passive maker limit.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(str, Enum):
    GTC = "GTC"
    POST_ONLY = "POST_ONLY"   # maker-only; rejected if it would take liquidity


@dataclass(slots=True)
class OrderIntent:
    """A high-level desire to reach a target exposure for a symbol."""

    symbol: str
    side: OrderSide
    quantity: float
    reason: str = ""
    reduce_only: bool = False
    created_ms: int = field(default_factory=lambda: int(time.time() * 1000))


@dataclass(slots=True)
class ChildOrder:
    """A single passive limit slice emitted by an execution algorithm."""

    symbol: str
    side: OrderSide
    quantity: float
    limit_price: float
    tif: TimeInForce = TimeInForce.POST_ONLY
    slice_index: int = 0
    slice_total: int = 1
    algo: str = "twap"
