"""ECONITH :: econith.data.providers.base

Native data-provider abstraction. Internalizes the OpenBB "universal provider"
idea as a thin normalization layer with zero heavy dependencies: a provider
turns arbitrary raw records into a uniform :class:`DataSeriesRow` stream that the
collectors / feature store consume.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

__all__ = ["DataSeriesRow", "MacroProvider", "TradfiProvider"]


@dataclass(slots=True)
class DataSeriesRow:
    symbol: str
    channel: str
    value: float
    ts_ms: int
    meta: dict[str, Any]

    def payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "channel": self.channel,
            "value": self.value,
            "ts_ms": self.ts_ms,
            "meta": self.meta,
        }


class _BaseProvider:
    """Normalize raw records into DataSeriesRow. Subclasses set ``asset_class``."""

    asset_class: str = "generic"

    def normalize(self, records: Iterable[dict[str, Any]]) -> list[DataSeriesRow]:
        out: list[DataSeriesRow] = []
        for r in records:
            try:
                out.append(
                    DataSeriesRow(
                        symbol=str(r.get("symbol", "")).upper(),
                        channel=str(r.get("channel", self.asset_class)),
                        value=float(r.get("value", 0.0)),
                        ts_ms=int(r.get("ts_ms", 0)),
                        meta={k: v for k, v in r.items()
                              if k not in {"symbol", "channel", "value", "ts_ms"}},
                    )
                )
            except (TypeError, ValueError):
                continue
        return out


class MacroProvider(_BaseProvider):
    asset_class = "macro"


class TradfiProvider(_BaseProvider):
    asset_class = "tradfi"
