"""ECONITH :: quant.payloads

Pydantic V2 execution payload contracts for the QUANT trading desk.

These are the deterministic, exchange-agnostic order intents produced by a
sliced sub-brain and consumed by the CCXT Binance bridge. The schema is strict
(``extra="forbid"``) so a malformed intent can never reach live capital.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "OrderSide",
    "OrderType",
    "ExecutionAlgo",
    "PositionDelta",
    "AlgoSlice",
    "ExecutionPayload",
    "CCXTOrderPayload",
]


class OrderSide(str, Enum):
    """Position-lifecycle-aware sides used throughout the cockpit ledger."""

    LONG_OPEN = "LONG_OPEN"
    LONG_CLOSE = "LONG_CLOSE"
    SHORT_OPEN = "SHORT_OPEN"
    SHORT_CLOSE = "SHORT_CLOSE"

    @property
    def ccxt_side(self) -> str:
        """Reduce to CCXT's binary buy/sell."""
        return "buy" if self in (OrderSide.LONG_OPEN, OrderSide.SHORT_CLOSE) else "sell"

    @property
    def reduce_only(self) -> bool:
        return self in (OrderSide.LONG_CLOSE, OrderSide.SHORT_CLOSE)


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class ExecutionAlgo(str, Enum):
    """Algorithmic execution scheme for large notional slicing."""

    IMMEDIATE = "IMMEDIATE"
    TWAP = "TWAP"
    VWAP = "VWAP"


class PositionDelta(BaseModel):
    """The target change in exposure a sub-brain wants realised."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    target_weight: Annotated[float, Field(ge=-1.0, le=1.0)]
    current_weight: Annotated[float, Field(ge=-1.0, le=1.0)] = 0.0

    @property
    def delta(self) -> float:
        return self.target_weight - self.current_weight


class AlgoSlice(BaseModel):
    """A single child order emitted by a TWAP/VWAP slicer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: Annotated[int, Field(ge=0)]
    weight: Annotated[float, Field(gt=0.0, le=1.0)]
    scheduled_offset_ms: Annotated[int, Field(ge=0)]
    quantity: Annotated[float, Field(gt=0.0)]


class ExecutionPayload(BaseModel):
    """The full, deterministic execution intent for one symbol on one tick."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    symbol: str
    desk: str
    mode: str  # "REALITY" | "SIMULATION"
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    algo: ExecutionAlgo = ExecutionAlgo.IMMEDIATE
    quantity: Annotated[float, Field(gt=0.0)]
    limit_price: float | None = None
    leverage: Annotated[float, Field(ge=1.0, le=125.0)] = 1.0
    target_position_delta: float = 0.0
    slices: tuple[AlgoSlice, ...] = ()
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    macro_regime: str = "UNKNOWN"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    client_order_id: str = ""

    @model_validator(mode="after")
    def _validate_consistency(self) -> "ExecutionPayload":
        if self.order_type is OrderType.LIMIT and self.limit_price is None:
            raise ValueError("LIMIT orders require a limit_price")
        if self.algo in (ExecutionAlgo.TWAP, ExecutionAlgo.VWAP) and not self.slices:
            raise ValueError(f"{self.algo.value} execution requires slices")
        if self.slices:
            total = sum(s.weight for s in self.slices)
            if not (0.999 <= total <= 1.001):
                raise ValueError(f"slice weights must sum to 1.0, got {total:.4f}")
        return self


class CCXTOrderPayload(BaseModel):
    """The concrete kwargs handed to ``ccxt.binance.create_order``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str                 # CCXT unified symbol, e.g. "BTC/USDT"
    type: str                   # "market" | "limit"
    side: str                   # "buy" | "sell"
    amount: Annotated[float, Field(gt=0.0)]
    price: float | None = None
    params: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_execution(
        cls, payload: ExecutionPayload, ccxt_symbol: str
    ) -> "CCXTOrderPayload":
        """Lower a high-level :class:`ExecutionPayload` into CCXT kwargs."""
        params: dict[str, object] = {
            "reduceOnly": payload.side.reduce_only,
            "newClientOrderId": payload.client_order_id or None,
        }
        if payload.leverage > 1.0:
            params["leverage"] = payload.leverage
        return cls(
            symbol=ccxt_symbol,
            type=payload.order_type.value.lower(),
            side=payload.side.ccxt_side,
            amount=payload.quantity,
            price=payload.limit_price,
            params={k: v for k, v in params.items() if v is not None},
        )
