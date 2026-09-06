"""ECONITH VPS Collector :: config

Static configuration schemas and constants for the standalone crypto
"Data Factory" that runs 24/7 on a lightweight remote Linux VPS.

This module is fully self-contained: it imports nothing from the wider ECONITH
platform. It defines the asset universe, the Binance combined-stream topology,
the desk taxonomy used for raw-lake partitioning, and every tunable knob for
buffering, flushing, and reconnection backoff.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================
#  Asset universe -- exactly 10 balanced tokens across three trading desks.
#  NOTE: legacy typo 'soldusdt' corrected to 'solusdt'.
# ============================================================================
CRYPTO_MAJORS: tuple[str, ...] = (
    "btcusdt",
    "ethusdt",
)

# High-Beta / Tech L1 / AI basket.
CRYPTO_HIGH_BETA: tuple[str, ...] = (
    "solusdt",
    "avaxusdt",
    "nearusdt",
    "suiusdt",
    "fetusdt",
    "renderusdt",
)

# Meme / speculative basket.
CRYPTO_MEME: tuple[str, ...] = (
    "dogeusdt",
    "pepeusdt",
)

# Full tracked universe in a deterministic order (used to build the WS topology).
SYMBOLS: tuple[str, ...] = CRYPTO_MAJORS + CRYPTO_HIGH_BETA + CRYPTO_MEME

# Desk membership keyed by the partition directory name. Uppercased so lookups
# match the normalised symbol carried on every frame (Binance sends e.g. 'BTCUSDT').
DESK_MEMBERSHIP: dict[str, tuple[str, ...]] = {
    "crypto_majors": tuple(s.upper() for s in CRYPTO_MAJORS),
    "crypto_high_beta": tuple(s.upper() for s in CRYPTO_HIGH_BETA),
    "crypto_meme": tuple(s.upper() for s in CRYPTO_MEME),
}

# Reverse index: SYMBOL -> desk, built once at import for O(1) resolution.
_SYMBOL_TO_DESK: dict[str, str] = {
    sym: desk for desk, members in DESK_MEMBERSHIP.items() for sym in members
}

# Fallback bucket for anything not explicitly mapped (should never trigger in
# production but keeps the writer total on a rogue frame).
UNCLASSIFIED_DESK: str = "crypto_unclassified"


def resolve_desk(symbol: str) -> str:
    """Resolve the desk partition bucket for a symbol (case-insensitive)."""
    return _SYMBOL_TO_DESK.get(symbol.upper(), UNCLASSIFIED_DESK)


# ============================================================================
#  Stream topology -- required combined streams per symbol.
# ============================================================================
#  * aggTrade        : aggregated trades (order-flow tape)
#  * depth20@100ms   : top-20 order-book levels, refreshed every 100ms
#  * markPrice@1s    : mark price + funding, refreshed every second
STREAMS: tuple[str, ...] = (
    "aggTrade",
    "depth20@100ms",
    "markPrice@1s",
)

# Binance USD-M Futures combined-stream endpoint.
WS_BASE: str = "wss://fstream.binance.com/stream"

# Top-level asset class for the raw lake (this factory harvests crypto only).
ASSET_CLASS: str = "market"

# Upstream provider tag stamped on every persisted row.
SOURCE: str = "binance"


@dataclass(slots=True)
class CollectorConfig:
    """Runtime configuration for the VPS crypto collector.

    Every field has a production-safe default; override via the constructor for
    tests or alternate deployments. The dataclass is intentionally the single
    source of truth for the daemon and storage layers.
    """

    # -- universe & topology --------------------------------------------------
    symbols: tuple[str, ...] = SYMBOLS
    streams: tuple[str, ...] = STREAMS
    ws_base: str = WS_BASE

    # -- storage --------------------------------------------------------------
    data_root: Path = Path("datasets/raw")
    # Flush when the buffer reaches this many rows...
    flush_threshold: int = 2_000
    # ...or when this many seconds elapse, whichever comes first.
    flush_interval_s: float = 5.0

    # -- reconnection / fault tolerance --------------------------------------
    backoff_base_s: float = 1.0      # first retry delay
    backoff_max_s: float = 60.0      # ceiling for exponential growth
    backoff_jitter: float = 0.3      # +/- proportional jitter to avoid thundering herd
    backoff_exp_cap: int = 6         # cap the 2**n exponent (2**6 = 64x base)

    # -- websocket keepalive --------------------------------------------------
    ws_ping_interval_s: float = 15.0
    ws_ping_timeout_s: float = 10.0
    ws_open_timeout_s: float = 10.0

    # -- observability --------------------------------------------------------
    heartbeat_s: float = 30.0

    def __post_init__(self) -> None:
        # Normalise root to a Path even if a str was supplied.
        self.data_root = Path(self.data_root)
        self.flush_threshold = max(1, int(self.flush_threshold))

    def combined_stream_url(self) -> str:
        """Build the full Binance combined-stream websocket URL.

        Example fragment::

            wss://fstream.binance.com/stream?streams=btcusdt@aggTrade/btcusdt@depth20@100ms/...
        """
        parts = [f"{sym}@{stream}" for sym in self.symbols for stream in self.streams]
        return f"{self.ws_base}?streams={'/'.join(parts)}"

    @property
    def stream_count(self) -> int:
        return len(self.symbols) * len(self.streams)
