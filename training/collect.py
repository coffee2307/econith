"""ECONITH :: training.collect  (PHASE A -- Data Collection)

Mine the raw material for every model downstream.

Economic analogy
----------------
Before a factory can build anything, it needs a steady supply of raw material.
This script is the **mine + conveyor belt**: it taps Binance, and for every
"heartbeat" of the market (each order-book refresh) it stamps out one *feature
row* -- a small standardised crate of data -- and stacks 500 crates per pallet
(`features_XXXXX.parquet`). Those pallets are what the labeling and training
stages consume later.

Two ways to fill the warehouse:

* **Live collection** (default): ride the real-time Binance stream and record
  the market as it happens. This reuses the EXACT same production code path the
  running backend trades on (streamer -> order-flow pipeline -> FeatureBuilder),
  so what you record is precisely what the system "sees" live. No credentials?
  The streamer transparently falls back to a high-fidelity mock so you can test
  the whole conveyor belt before real keys arrive.

* **Backfill** (`--backfill`): download historical OHLCV candles (public data,
  no signing needed) to seed the warehouse with months of past market history in
  minutes. Useful because a fresh live stream starts empty -- backfill gives the
  models a "history book" to study.

Run it:
    python training/collect.py --symbol BTCUSDT --output ./datasets/features
    python training/collect.py --backfill --symbol BTCUSDT --start 2024-01-01 --end 2025-12-31
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

# When launched as `python training/collect.py`, make the project root importable
# so `infrastructure...`, `core...`, `config...` resolve exactly like the backend.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.environment import get_environment  # noqa: E402
from core.engine import TimeEngine  # noqa: E402
from core.event_bus import Event, EventBus  # noqa: E402
from infrastructure.alternative.provider import AlternativeDataProvider  # noqa: E402
from infrastructure.feature_store.builder import FeatureBuilder  # noqa: E402
from infrastructure.feature_store.writer import FeatureWriter  # noqa: E402
from infrastructure.preprocessing.pipeline import MarketDataPipeline  # noqa: E402
from infrastructure.websocket.streamer import BinanceWebSocketStreamer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.collect")

# Public (read-only) market-data host for historical candles. Backfill needs no
# API key -- it is the same public price history anyone can download -- so we go
# straight to the production data host regardless of the trading testnet setting.
PUBLIC_DATA_HOST = "https://api.binance.com"

# Binance kline column order (the 12 fields it returns per candle).
_KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote", "ignore",
]


# ===========================================================================
#  LIVE COLLECTOR
# ===========================================================================
class LiveCollector:
    """Consolidates the live market feed into batched feature-row pallets.

    The order-flow pipeline publishes several partial views of the market on the
    EventBus (price here, order-book imbalance there, funding rate elsewhere).
    This collector keeps the latest value of each on a small "workbench"
    (``_market`` / ``_alt``) and, on every order-book refresh, assembles them
    into one complete crate (a feature row) and hands it to the FeatureWriter.

    Sampling on the order-book beat (``indicator.obi``) gives an even, ~100ms
    cadence -- like taking one photo of the market every heartbeat.
    """

    def __init__(
        self,
        bus: EventBus,
        writer: FeatureWriter,
        builder: FeatureBuilder,
        symbol: str,
    ) -> None:
        self._bus = bus
        self._writer = writer
        self._builder = builder
        self._symbol = symbol.upper()
        # Rolling "latest known value" workbench for each data stream.
        self._market: dict[str, Any] = {"symbol": self._symbol}
        self._alt: dict[str, Any] = {}
        self._rows = 0

    def register(self) -> None:
        # Subscribe to the DERIVED topics (same ones Sentinel + the dashboard use)
        # rather than raw frames, so every crate is already cleaned + enriched.
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("indicator.volume_delta", self._on_volume_delta)
        self._bus.subscribe("indicator.obi", self._on_obi)
        self._bus.subscribe("alt.funding_rate", self._on_funding)
        self._bus.subscribe("alt.open_interest", self._on_open_interest)
        self._bus.subscribe("alt.liquidation", self._on_liquidation)

    # -- stream handlers: each just updates the workbench ---------------------
    async def _on_ticker(self, event: Event) -> None:
        self._market["price"] = event.payload.get("price")

    async def _on_volume_delta(self, event: Event) -> None:
        p = event.payload
        self._market["volume_delta"] = p.get("volume_delta")
        self._market["buy_volume"] = p.get("buy_volume")
        self._market["sell_volume"] = p.get("sell_volume")
        self._market["trade_count"] = p.get("trade_count")

    async def _on_funding(self, event: Event) -> None:
        p = event.payload
        self._alt["funding_rate"] = p.get("funding_rate")
        self._alt["time_to_funding_s"] = p.get("time_to_funding_s")

    async def _on_open_interest(self, event: Event) -> None:
        p = event.payload
        self._alt["open_interest"] = p.get("open_interest")
        self._alt["oi_change_pct"] = p.get("oi_change_pct")

    async def _on_liquidation(self, event: Event) -> None:
        self._alt["total_notional"] = event.payload.get("total_notional")

    async def _on_obi(self, event: Event) -> None:
        # The order book just refreshed -> take one snapshot of the whole market.
        p = event.payload
        self._market["obi"] = p.get("obi")
        self._market["bid_volume"] = p.get("bid_volume")
        self._market["ask_volume"] = p.get("ask_volume")
        self._market["mid"] = p.get("mid")
        self._market["best_bid"] = p.get("best_bid")
        self._market["best_ask"] = p.get("best_ask")

        # Don't record until we actually have a price (avoids empty first crates).
        if self._market.get("price") is None and self._market.get("mid") is None:
            return

        row = self._builder.build(self._market, self._alt)
        # Stamp wall-clock time: Phase B needs it to look "into the future" and
        # measure forward returns. Without a timestamp the history has no arrow.
        row["ts_ms"] = int(time.time() * 1000)
        self._writer.add(row)
        self._rows += 1
        if self._rows % 500 == 0:
            logger.info(
                "collected %d rows (%d flushed to disk)",
                self._rows, self._writer.total_written,
            )

    @property
    def rows(self) -> int:
        return self._rows


async def run_live(
    symbol: str,
    output: str,
    batch_size: int,
    duration: float,
) -> int:
    """Boot the live pipeline and record until ``duration`` seconds elapse.

    ``duration <= 0`` means "run forever" (until Ctrl-C) -- the usual mode for a
    24/7 collector sitting in a tmux/screen session accumulating history.
    """
    env = get_environment()
    mode = "LIVE" if env.has_binance_data_credentials else "MOCK"
    logger.info("starting collector [%s] symbol=%s -> %s", mode, symbol, output)

    bus = EventBus()
    # A TimeEngine is needed so the MOCK streamer has a clock to beat to; in LIVE
    # mode it is harmless. We keep it at 1x -- real wall-clock cadence.
    time_engine = TimeEngine(bus, multiplier=1)

    pipeline = MarketDataPipeline(bus)
    pipeline.register()

    writer = FeatureWriter(dataset="features", batch_size=batch_size, root=output)
    builder = FeatureBuilder()
    collector = LiveCollector(bus, writer, builder, symbol)
    collector.register()

    alt = AlternativeDataProvider(bus, symbol=symbol)
    alt.register()
    streamer = BinanceWebSocketStreamer(bus, time_engine, symbol=symbol)

    await bus.start()
    await time_engine.start()
    await alt.start()
    await streamer.start()

    # Graceful shutdown: flush the half-full pallet so no crate is lost on exit.
    stop = asyncio.Event()

    def _request_stop(*_: Any) -> None:
        logger.info("stop signal received -- draining buffers")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            # Windows: add_signal_handler is unsupported -> KeyboardInterrupt path.
            pass

    started = time.monotonic()
    try:
        while not stop.is_set():
            await asyncio.sleep(0.5)
            if duration > 0 and (time.monotonic() - started) >= duration:
                logger.info("duration %.0fs reached -- stopping", duration)
                break
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("interrupted -- draining buffers")
    finally:
        await streamer.stop()
        await alt.stop()
        await time_engine.stop()
        writer.flush()          # persist the remaining (<batch_size) rows
        await bus.stop()

    logger.info(
        "collection complete: %d rows recorded, %d written to %s",
        collector.rows, writer.total_written, output,
    )
    return collector.rows


# ===========================================================================
#  HISTORICAL BACKFILL
# ===========================================================================
def run_backfill(
    symbol: str,
    start: str,
    end: str,
    intervals: list[str],
    output: str,
    base_url: str,
) -> int:
    """Download historical OHLCV candles into ``output`` as one Parquet per interval.

    Economic analogy: live collection is watching the market from today onward;
    backfill is buying the *history book* so your models can study years of past
    "weather" before they ever place a bet. This is public data -- no API key or
    signature required -- so it always hits the public data host.
    """
    import httpx  # local import: keeps the live path free of this dependency
    import pandas as pd

    start_ms = _to_millis(start)
    end_ms = _to_millis(end)
    out_dir = Path(output)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    with httpx.Client(base_url=base_url, timeout=20.0) as client:
        for interval in intervals:
            rows = _fetch_klines(client, symbol, interval, start_ms, end_ms)
            if not rows:
                logger.warning("no candles for %s %s", symbol, interval)
                continue
            df = pd.DataFrame(rows, columns=_KLINE_COLS)
            # Keep the columns a human (and the labeler) actually cares about,
            # cast prices/volumes to real numbers instead of Binance's strings.
            df = df[["open_time", "open", "high", "low", "close", "volume", "trades"]]
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            path = out_dir / f"{symbol.upper()}_{interval}.parquet"
            df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
            logger.info("backfilled %d candles -> %s", len(df), path)
            total += len(df)
    logger.info("backfill complete: %d candles total", total)
    return total


def _fetch_klines(
    client: Any, symbol: str, interval: str, start_ms: int, end_ms: int
) -> list[list[Any]]:
    """Page through /api/v3/klines 1000 candles at a time until we reach ``end``.

    Binance caps each request at 1000 candles, so we walk forward in windows,
    each time resuming just after the last candle we received -- like turning
    the pages of the history book one thousand days at a time.
    """
    out: list[list[Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        resp = client.get(
            "/api/v3/klines",
            params={
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        last_open = batch[-1][0]
        # Advance one millisecond past the last candle's open to avoid re-fetching.
        cursor = last_open + 1
        if len(batch) < 1000:
            break  # partial page => we've reached the present / the end
    return out


def _to_millis(date_str: str) -> int:
    """Parse a YYYY-MM-DD (or full ISO) string into epoch milliseconds (UTC)."""
    from datetime import datetime, timezone

    s = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit(f"unrecognised date '{date_str}' (use YYYY-MM-DD)")


# ===========================================================================
#  CLI
# ===========================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="collect.py",
        description="ECONITH Phase A -- collect live or historical Binance data.",
    )
    p.add_argument("--symbol", default="BTCUSDT", help="trading pair (default BTCUSDT)")
    p.add_argument(
        "--output",
        default="./datasets/features",
        help="live: feature-store dir; backfill: raw candle dir",
    )
    p.add_argument("--batch-size", type=int, default=500, help="rows per Parquet pallet")
    p.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="live seconds to run (0 = run until Ctrl-C)",
    )
    p.add_argument("--backfill", action="store_true", help="download historical OHLCV instead")
    p.add_argument("--start", default="2024-01-01", help="backfill start date (YYYY-MM-DD)")
    p.add_argument("--end", default="2025-12-31", help="backfill end date (YYYY-MM-DD)")
    p.add_argument(
        "--intervals",
        default="1m,5m",
        help="comma-separated kline intervals for backfill (e.g. 1m,5m,15m)",
    )
    p.add_argument(
        "--base-url",
        default=PUBLIC_DATA_HOST,
        help="public data host for backfill klines",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.backfill:
        # Backfill defaults its output to the raw dir if the user left the live default.
        out = args.output
        if out.rstrip("/\\").endswith("features"):
            out = "./datasets/raw/binance"
            logger.info("backfill output redirected to %s", out)
        intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
        run_backfill(args.symbol, args.start, args.end, intervals, out, args.base_url)
        return 0

    asyncio.run(
        run_live(
            symbol=args.symbol,
            output=args.output,
            batch_size=args.batch_size,
            duration=args.duration,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
