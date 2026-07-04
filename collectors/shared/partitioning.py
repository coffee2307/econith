"""ECONITH :: collectors.shared.partitioning

Deterministic partition-path resolution for the raw data lake.

Layout contract (matches docs/RESTRUCTURE_BLUEPRINT.md):

    datasets/raw/<asset_class>/<desk>/<symbol>/<YYYY-MM-DD>/<channel>_<hour>.parquet

The desk taxonomy mirrors ``core.ingestion.context_state.AssetUniverse`` but is
duplicated here (as a plain map) so the collectors package stays fully
standalone — it must import nothing from the heavy runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Desk membership for crypto symbols. Kept in sync with AssetUniverse but local
# so collectors have zero project-runtime imports.
_CRYPTO_DESKS: dict[str, tuple[str, ...]] = {
    "crypto_majors": ("BTCUSDT", "ETHUSDT"),
    "crypto_high_beta": ("SOLUSDT", "AVAXUSDT", "NEARUSDT", "SUIUSDT"),
    "crypto_meme": ("DOGEUSDT", "SHIBUSDT", "PEPEUSDT"),
}

_TRADFI_DESKS: dict[str, tuple[str, ...]] = {
    "fx": ("DXY", "USDCNY", "USDJPY", "EURUSD"),
    "commodities": ("XAUUSD", "XAGUSD", "WTIUSD", "BRENTUSD", "GOLD", "OIL"),
    "equities": ("SPX500", "NDX100", "HSI", "US10Y"),
}


def resolve_asset_class(symbol: str, hint: str | None = None) -> str:
    """Resolve the top-level asset class for a symbol.

    A ``hint`` (from the collector that produced it) always wins; otherwise we
    infer crypto vs tradfi from the desk maps, defaulting to ``market``.
    """
    if hint:
        return hint
    sym = symbol.upper()
    for members in _CRYPTO_DESKS.values():
        if sym in members:
            return "market"
    for members in _TRADFI_DESKS.values():
        if sym in members:
            return "tradfi"
    return "market"


def resolve_desk(symbol: str, asset_class: str) -> str:
    """Resolve the desk bucket for a symbol within its asset class."""
    sym = symbol.upper()
    table = _CRYPTO_DESKS if asset_class == "market" else _TRADFI_DESKS
    for desk, members in table.items():
        if sym in members:
            return desk
    if asset_class == "macro":
        return "series"
    return "unclassified"


@dataclass(slots=True, frozen=True)
class PartitionKey:
    """The tuple that uniquely identifies a raw partition file."""

    asset_class: str
    desk: str
    symbol: str
    date: str          # YYYY-MM-DD (UTC)
    hour: str          # HH (UTC)
    channel: str

    @classmethod
    def from_tick(cls, ts_ms: int, asset_class: str, symbol: str, channel: str) -> "PartitionKey":
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        return cls(
            asset_class=asset_class,
            desk=resolve_desk(symbol, asset_class),
            symbol=symbol.upper(),
            date=dt.strftime("%Y-%m-%d"),
            hour=dt.strftime("%H"),
            channel=channel,
        )


def partition_path(root: Path | str, key: PartitionKey) -> Path:
    """Build the absolute Parquet file path for a partition key."""
    return (
        Path(root)
        / key.asset_class
        / key.desk
        / key.symbol
        / key.date
        / f"{key.channel}_{key.hour}.parquet"
    )
