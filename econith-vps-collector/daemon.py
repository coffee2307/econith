"""ECONITH VPS Collector :: daemon

Main asyncio WebSocket manager & stream lifecycle for the standalone crypto
Data Factory.

Responsibilities
----------------
* Maintain a single Binance combined-stream websocket carrying every
  (symbol x channel) topic for the configured 10-token universe.
* Normalise each frame into a :class:`MarketRecord` and buffer it in RAM.
* Trigger flushes on the 2,000-row threshold *and* on a 5-second timer.
* Survive network drops, exchange disconnects, and rate limits via an
  exponential-backoff-with-jitter reconnect loop -- flushing the RAM buffer to
  disk before every reconnect so no memory state is lost on a clean restart.
* Shut down cleanly on SIGINT/SIGTERM, draining the buffer to disk.

Run directly on the VPS::

    python daemon.py
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import signal
import time

import websockets

from config import CollectorConfig
from storage import MarketRecord, ParquetSnapshotWriter

logger = logging.getLogger("econith.vps.daemon")

__all__ = ["CollectorDaemon", "main"]

# Scalar price fields, in priority order, for the optional ``value`` column.
_PRICE_KEYS: tuple[str, ...] = ("p", "c", "markPrice")


class CollectorDaemon:
    """Self-healing websocket consumer that persists normalised market frames."""

    def __init__(self, config: CollectorConfig | None = None) -> None:
        self._cfg = config or CollectorConfig()
        self._writer = ParquetSnapshotWriter(
            self._cfg.data_root, flush_threshold=self._cfg.flush_threshold
        )
        self._running = False
        self._failures = 0
        self._messages = 0
        self._rng = random.Random()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def messages(self) -> int:
        return self._messages

    # -- lifecycle ------------------------------------------------------------
    async def run_forever(self) -> None:
        """Start all loops and block until a termination signal arrives."""
        self._running = True
        logger.info(
            "collector starting: %d symbols x %d streams = %d topics -> %s",
            len(self._cfg.symbols), len(self._cfg.streams),
            self._cfg.stream_count, self._cfg.data_root,
        )

        self._tasks = [
            asyncio.create_task(self._consume_loop(), name="ws-consume"),
            asyncio.create_task(self._flush_loop(), name="periodic-flush"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat"),
        ]

        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)

        await stop_event.wait()
        await self._shutdown()

    async def _shutdown(self) -> None:
        """Cancel loops and perform a final buffer drain to disk."""
        logger.info("shutdown requested -- draining buffer and stopping")
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        drained = await self._writer.close()
        logger.info(
            "collector stopped (msgs=%d final_flush=%d total_written=%d)",
            self._messages, drained, self._writer.written,
        )

    # -- receive loop with exponential backoff --------------------------------
    async def _consume_loop(self) -> None:
        url = self._cfg.combined_stream_url()
        while self._running:
            try:
                await self._consume(url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - every network fault is recoverable
                self._failures += 1
                # Critical: never lose RAM state across a reconnect.
                flushed = await self._writer.flush()
                delay = self._backoff()
                logger.warning(
                    "ws fault #%d (%s); flushed %d buffered rows; reconnecting in %.1fs",
                    self._failures, exc, flushed, delay,
                )
                await asyncio.sleep(delay)

    def _backoff(self) -> float:
        """Exponential backoff with proportional jitter, clamped to the ceiling."""
        exp = min(
            self._cfg.backoff_max_s,
            self._cfg.backoff_base_s * (2 ** min(self._failures, self._cfg.backoff_exp_cap)),
        )
        jitter = self._rng.uniform(0.0, exp * self._cfg.backoff_jitter)
        return exp + jitter

    async def _consume(self, url: str) -> None:
        """Open one websocket session and pump frames until it drops."""
        async with websockets.connect(
            url,
            ping_interval=self._cfg.ws_ping_interval_s,
            ping_timeout=self._cfg.ws_ping_timeout_s,
            open_timeout=self._cfg.ws_open_timeout_s,
            max_queue=None,
        ) as ws:
            logger.info("websocket connected (%d topics)", self._cfg.stream_count)
            self._failures = 0  # healthy connection resets the backoff ladder
            async for raw in ws:
                if self._ingest(raw):
                    # Threshold tripped -- drain immediately to bound memory.
                    await self._writer.flush()

    # -- frame normalisation --------------------------------------------------
    def _ingest(self, raw: str | bytes) -> bool:
        """Parse, normalise, and buffer one raw frame. Returns the flush signal."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return False

        data = msg.get("data", msg)
        if not isinstance(data, dict):
            return False
        stream = msg.get("stream", "")

        symbol = str(data.get("s") or (stream.split("@")[0] if stream else "")).upper()
        if not symbol:
            return False
        channel = str(
            data.get("e") or (stream.split("@")[1] if "@" in stream else "unknown")
        )

        record = MarketRecord(
            ts_ms=int(data.get("E") or data.get("T") or time.time() * 1000),
            symbol=symbol,
            channel=channel,
            value=self._extract_price(data),
            payload=data,
        )
        self._messages += 1
        return self._writer.add(record)

    @staticmethod
    def _extract_price(data: dict) -> float | None:
        for key in _PRICE_KEYS:
            v = data.get(key)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return None
        return None

    # -- periodic flush + heartbeat ------------------------------------------
    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.flush_interval_s)
            await self._writer.flush()

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._cfg.heartbeat_s)
            logger.info(
                "heartbeat msgs=%d buffered=%d written=%d failures=%d",
                self._messages, self._writer.buffered,
                self._writer.written, self._failures,
            )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    asyncio.run(CollectorDaemon().run_forever())


if __name__ == "__main__":
    main()
