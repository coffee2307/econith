"""ECONITH :: collectors.shared.schemas

Strict, dependency-free validation contracts for cross-asset telemetry.

Every collector (crypto, macro, tradfi) normalises its raw upstream payload into
a :class:`CrossAssetTick` before it is buffered/persisted. Using a frozen
dataclass keeps the hot path allocation-light while still giving a single,
auditable schema and a hard finiteness/þype guarantee so a poisoned upstream
frame can never silently reach the training tier.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class AssetClass(str, Enum):
    """Top-level data-plane taxonomy used for raw partitioning."""

    MARKET = "market"        # crypto order-flow / ticks / orderbook
    MACRO = "macro"          # FRED / World Bank / IMF / Eurostat
    TRADFI = "tradfi"        # DXY, gold, equities, crude


ASSET_CLASSES: frozenset[str] = frozenset(a.value for a in AssetClass)


class ValidationError(ValueError):
    """Raised when a normalised tick violates the schema contract."""


@dataclass(slots=True, frozen=True)
class CrossAssetTick:
    """One normalised, time-stamped observation destined for cold storage.

    Fields:
        ts_ms:       absolute UTC epoch milliseconds (the universal join key)
        asset_class: one of :class:`AssetClass`
        symbol:      instrument / series identifier (e.g. BTCUSDT, FEDFUNDS, DXY)
        channel:     stream/metric sub-type (e.g. aggTrade, depth20, cpi)
        source:      upstream provider (e.g. binance, fred, yfinance)
        value:       primary scalar (price/rate/index); optional for book frames
        payload:     the raw normalised field map (kept for lossless replay)
    """

    ts_ms: int
    asset_class: str
    symbol: str
    channel: str
    source: str
    value: Optional[float] = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        """Flatten into a columnar-friendly dict (payload JSON-encoded)."""
        import json

        return {
            "ts_ms": self.ts_ms,
            "asset_class": self.asset_class,
            "symbol": self.symbol,
            "channel": self.channel,
            "source": self.source,
            "value": self.value,
            "payload": json.dumps(self.payload, separators=(",", ":"), default=str),
        }


def _is_finite_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_tick(tick: CrossAssetTick) -> CrossAssetTick:
    """Validate a tick, raising :class:`ValidationError` on any contract breach.

    Returns the tick unchanged on success so it composes in a pipeline
    (``writer.add(validate_tick(t))``).
    """
    if not isinstance(tick.ts_ms, int) or tick.ts_ms <= 0:
        raise ValidationError(f"ts_ms must be a positive int, got {tick.ts_ms!r}")
    # Reject absurd timestamps (before 2015 or >1 day in the future) which almost
    # always signal a unit mix-up (seconds vs millis) upstream.
    now_ms = int(time.time() * 1000)
    if tick.ts_ms < 1_420_000_000_000 or tick.ts_ms > now_ms + 86_400_000:
        raise ValidationError(f"ts_ms {tick.ts_ms} outside sane epoch-ms range")
    if tick.asset_class not in ASSET_CLASSES:
        raise ValidationError(f"unknown asset_class {tick.asset_class!r}")
    if not tick.symbol or not isinstance(tick.symbol, str):
        raise ValidationError("symbol must be a non-empty string")
    if not tick.channel or not isinstance(tick.channel, str):
        raise ValidationError("channel must be a non-empty string")
    if tick.value is not None and not _is_finite_number(tick.value):
        raise ValidationError(f"value must be finite or None, got {tick.value!r}")
    if not isinstance(tick.payload, dict):
        raise ValidationError("payload must be a dict")
    return tick


def make_tick(
    *,
    asset_class: str | AssetClass,
    symbol: str,
    channel: str,
    source: str,
    value: Optional[float] = None,
    ts_ms: Optional[int] = None,
    payload: Optional[dict[str, Any]] = None,
) -> CrossAssetTick:
    """Convenience constructor that stamps ``ts_ms`` (now) when omitted."""
    ac = asset_class.value if isinstance(asset_class, AssetClass) else str(asset_class)
    return CrossAssetTick(
        ts_ms=ts_ms if ts_ms is not None else int(time.time() * 1000),
        asset_class=ac,
        symbol=symbol.upper(),
        channel=channel,
        source=source,
        value=value,
        payload=payload or {},
    )
