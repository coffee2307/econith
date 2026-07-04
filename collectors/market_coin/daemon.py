"""ECONITH :: collectors.market_coin.daemon

Standalone, multi-symbol, 24/7 crypto order-flow collector.

A ground-up rework of the legacy ``infrastructure/daemon/vps_telemetry_daemon.py``
that persists via the shared Polars-based :class:`SnapshotWriter` into the
partitioned raw lake (``datasets/raw/market/<desk>/<symbol>/<date>/``).

Zero ML dependencies — only ``websockets`` (optional; synthetic tape fallback),
plus the ``collectors.shared`` primitives. Designed to run under systemd/tmux on
a low-spec VPS.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path

from collectors.shared.persistence import SnapshotWriter
from collectors.shared.schemas import AssetClass, CrossAssetTick, validate_tick

logger = logging.getLogger("econith.collectors.market_coin")

__all__ = ["MarketCoinConfig", "MarketCoinDaemon"]


@dataclass(slots=True)
class MarketCoinConfig:
    """Runtime configuration for the crypto collector."""

    symbols: tuple[str, ...] = ("btcusdt", "ethusdt", "soldusdt", "dogeusdt")
    streams: tuple[str, ...] = ("aggTrade", "depth20@100ms", "markPrice@1s")
    ws_base: str = "wss://fstream.binance.com/stream"
    data_root: Path = Path("datasets/raw")
    flush_interval_s: float = 5.0
    flush_threshold: int = 2_000
    backoff_base_s: float = 1.0
    backoff_max_s: float = 60.0
    reauth_after_failures: int = 5
    heartbeat_s: float = 30.0

    def stream_path(self) -> str:
        parts = [f"{sym}@{stream}" for sym in self.symbols for stream in self.streams]
        return "?streams=" + "/".join(parts)


class MarketCoinDaemon:
    """Self-healing WS consumer that normalises frames into CrossAssetTicks."""

    def __init__(self, config: MarketCoinConfig | None = None) -> None:
        self._cfg = config or MarketCoinConfig()
        self._writer = SnapshotWriter(
            self._cfg.data_root, flush_threshold=self._cfg.flush_threshold
        )
        self._running = False
        self._failures = 0
        self._messages = 0
        self._rng = random.Random(1337)
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def messages(self) -> int:
        return self._messages

    # -- lifecycle ------------------------------------------------------------
    async def start(self) -> None:
        self._running = True
        logger.info(
            "market_coin daemon starting (symbols=%s, root=%s)",
            self._cfg.symbols, self._cfg.data_root,
        )
        self._tasks = [
            asyncio.create_task(self._consume_loop(), name="market-ws"),
            asyncio.create_task(self._flush_loop(), name="market-flush"),
            asyncio.create_task(self._heartbeat(), name="market-heartbeat"),
        ]

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._writer.close()
        logger.info("market_coin daemon stopped (msgs=%d)", self._messages)

    async def run_forever(self) -> None:
        await self.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
        await self.stop()

    # -- receive loop with exponential backoff --------------------------------
    async def _consume_loop(self) -> None:
        url = self._cfg.ws_base + self._cfg.stream_path()
        while self._running:
            try:
                await self._consume(url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every network fault recoverable
                self._failures += 1
                delay = self._backoff()
                logger.warning(
                    "market WS fault #%d (%s); reconnecting in %.1fs",
                    self._failures, exc, delay,
                )
                if self._failures % self._cfg.reauth_after_failures == 0:
                    logger.info("re-establishing market session after repeated faults")
                await asyncio.sleep(delay)

    def _backoff(self) -> float:
        exp = min(
            self._cfg.backoff_max_s,
            self._cfg.backoff_base_s * (2 ** min(self._failures, 6)),
        )
        return exp + self._rng.uniform(0.0, exp * 0.3)

    async def _consume(self, url: str) -> None:
        try:
            import websockets
        except ImportError:
            logger.error("websockets not installed -- running synthetic tape")
            await self._synthetic_tape()
            return
        async with websockets.connect(url, ping_interval=15, ping_timeout=10) as ws:
            logger.info("market_coin connected: %s", url)
            self._failures = 0
            async for raw in ws:
                self._ingest(raw)

    def _ingest(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        data = msg.get("data", msg)
        stream = msg.get("stream", "")
        symbol = str(data.get("s") or stream.split("@")[0]).upper()
        channel = str(data.get("e") or (stream.split("@")[1] if "@" in stream else "unknown"))
        value = self._extract_price(data)
        tick = CrossAssetTick(
            ts_ms=int(data.get("E") or data.get("T") or time.time() * 1000),
            asset_class=AssetClass.MARKET.value,
            symbol=symbol,
            channel=channel,
            source="binance",
            value=value,
            payload=data if isinstance(data, dict) else {},
        )
        try:
            self._writer.add(validate_tick(tick))
            self._messages += 1
        except Exception as exc:  # noqa: BLE001 - a malformed frame must not kill the loop
            logger.debug("dropped malformed market frame: %s", exc)

    @staticmethod
    def _extract_price(data: dict) -> float | None:
        for key in ("p", "c", "markPrice"):
            v = data.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    async def _synthetic_tape(self) -> None:
        """Deterministic mock tape so the daemon is testable without network."""
        self._failures = 0
        base = {"BTCUSDT": 60_000.0, "ETHUSDT": 3_000.0, "SOLUSDT": 150.0, "DOGEUSDT": 0.12}
        while self._running:
            for sym in self._cfg.symbols:
                s = sym.upper()
                px = base.get(s, 100.0) * (1.0 + self._rng.uniform(-0.001, 0.001))
                tick = CrossAssetTick(
                    ts_ms=int(time.time() * 1000),
                    asset_class=AssetClass.MARKET.value,
                    symbol=s,
                    channel="aggTrade",
                    source="synthetic",
                    value=px,
                    payload={"p": px, "q": self._rng.random()},
                )
                self._writer.add(validate_tick(tick))
                self._messages += 1
            await asyncio.sleep(0.1)

    # -- flush + heartbeat ----------------------------------------------------
    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.flush_interval_s)
            await self._writer.flush()

    async def _heartbeat(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.heartbeat_s)
            logger.info(
                "market_coin heartbeat msgs=%d buffered=%d written=%d",
                self._messages, self._writer.buffered, self._writer.written,
            )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s"
    )
    asyncio.run(MarketCoinDaemon().run_forever())


if __name__ == "__main__":
    main()
