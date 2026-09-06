"""ECONITH :: infrastructure.preprocessing.cleaner

Raw market-data sanitisation (master plan, Phase 1, Step 1/3).

Converts noisy Binance JSON frames into validated, strongly-typed records:
  * string price/qty fields -> floats
  * malformed / non-positive / NaN rows dropped
  * order book levels sorted (bids desc, asks asc) and truncated to top-N

These typed records feed the indicator layer (OBI / Volume Delta).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger("econith.infra.preprocessing.cleaner")


@dataclass(slots=True, frozen=True)
class AggTrade:
    symbol: str
    agg_id: int
    price: float
    qty: float
    is_buyer_maker: bool          # True => aggressor SOLD into the bid
    event_ms: int
    trade_ms: int

    @property
    def is_aggressive_buy(self) -> bool:
        """Taker bought (lifted the ask) when the buyer is NOT the maker."""
        return not self.is_buyer_maker

    @property
    def signed_qty(self) -> float:
        """+qty for aggressive buys, -qty for aggressive sells."""
        return self.qty if self.is_aggressive_buy else -self.qty


@dataclass(slots=True, frozen=True)
class DepthSnapshot:
    symbol: str
    last_update_id: int
    bids: list[tuple[float, float]]   # [(price, qty), ...] sorted desc by price
    asks: list[tuple[float, float]]   # [(price, qty), ...] sorted asc by price

    @property
    def best_bid(self) -> float | None:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0


def _to_float(value: object) -> float | None:
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def clean_agg_trade(frame: dict) -> AggTrade | None:
    """Validate + type a raw ``@aggTrade`` frame. Returns ``None`` if invalid."""
    try:
        price = _to_float(frame["p"])
        qty = _to_float(frame["q"])
        if price is None or qty is None or price <= 0 or qty <= 0:
            return None
        return AggTrade(
            symbol=str(frame["s"]),
            agg_id=int(frame["a"]),
            price=price,
            qty=qty,
            is_buyer_maker=bool(frame["m"]),
            event_ms=int(frame.get("E", frame.get("T", 0))),
            trade_ms=int(frame.get("T", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        logger.debug("dropping malformed aggTrade: %s", exc)
        return None


def _clean_levels(
    raw: list, *, descending: bool, limit: int
) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        price = _to_float(entry[0])
        qty = _to_float(entry[1])
        if price is None or qty is None or price <= 0 or qty < 0:
            continue
        cleaned.append((price, qty))
    cleaned.sort(key=lambda lvl: lvl[0], reverse=descending)
    return cleaned[:limit]


def clean_depth(frame: dict, symbol: str, levels: int = 20) -> DepthSnapshot | None:
    """Validate + type a raw ``@depth20`` frame (spot partial book schema)."""
    try:
        bids = _clean_levels(frame.get("bids", []), descending=True, limit=levels)
        asks = _clean_levels(frame.get("asks", []), descending=False, limit=levels)
        if not bids or not asks:
            return None
        return DepthSnapshot(
            symbol=symbol,
            last_update_id=int(frame.get("lastUpdateId", 0)),
            bids=bids,
            asks=asks,
        )
    except (TypeError, ValueError) as exc:
        logger.debug("dropping malformed depth frame: %s", exc)
        return None
