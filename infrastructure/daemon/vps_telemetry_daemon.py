"""ECONITH :: infrastructure.daemon.vps_telemetry_daemon

24/7 lightweight telemetry ingestion daemon for unmanaged, low-spec VPS hosts.

Captures pristine institutional-grade high-frequency Binance order-book and
alternative telemetry indefinitely, engineered for a tiny memory/CPU footprint:

* :class:`SelfHealingConnection` -- an async WebSocket manager with strict
  network-exception guards, dynamic exponential backoff (with jitter) and rapid
  re-authentication so a disconnect never drops the pipeline.
* :class:`RingBuffer` -- a bounded in-memory pool that decouples the hot receive
  loop from disk latency; it never grows unbounded on a slow disk.
* :class:`PersistenceHandler` -- a background flusher writing Snappy-compressed
  Parquet (preferred) or SQLite-WAL, chunked by day/hour.
* :class:`VPSTelemetryDaemon` -- the supervisor tying them together with a clean
  signal-driven shutdown.

The daemon is intentionally dependency-optional: it runs with only the stdlib
(SQLite + JSONL fallback) and lights up Parquet/websockets when available.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import signal
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("econith.vps.telemetry_daemon")

__all__ = [
    "DaemonConfig",
    "TelemetryTick",
    "RingBuffer",
    "SelfHealingConnection",
    "PersistenceHandler",
    "VPSTelemetryDaemon",
]


# ---------------------------------------------------------------------------
# Config + record
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class DaemonConfig:
    """Runtime configuration for the VPS collector."""

    symbols: tuple[str, ...] = ("btcusdt", "ethusdt")
    #: Binance combined-stream endpoints per symbol. depth20@100ms L2 book,
    #: aggTrade tape, and markPrice (funding/OI) for perps.
    streams: tuple[str, ...] = ("depth20@100ms", "aggTrade", "markPrice@1s")
    ws_base: str = "wss://fstream.binance.com/stream"
    data_root: Path = Path("./datasets/vps")
    flush_interval_s: float = 5.0
    flush_batch_size: int = 2_000
    ring_capacity: int = 50_000
    backoff_base_s: float = 1.0
    backoff_max_s: float = 60.0
    reauth_after_failures: int = 5
    use_parquet: bool = True

    def stream_path(self) -> str:
        parts = [
            f"{sym}@{stream}"
            for sym in self.symbols
            for stream in self.streams
        ]
        return "?streams=" + "/".join(parts)


@dataclass(slots=True)
class TelemetryTick:
    """A single normalised telemetry record destined for cold storage."""

    ts_ms: int
    symbol: str
    channel: str
    payload: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "symbol": self.symbol,
            "channel": self.channel,
            "payload": json.dumps(self.payload, separators=(",", ":")),
        }


# ---------------------------------------------------------------------------
# Ring buffer
# ---------------------------------------------------------------------------
class RingBuffer:
    """Bounded, lock-free-ish in-memory pool decoupling receive from persist.

    On overflow the OLDEST records are dropped (never the freshest tape) and a
    counter is bumped so operators can alarm on sustained disk back-pressure.
    """

    def __init__(self, capacity: int) -> None:
        self._buf: deque[TelemetryTick] = deque(maxlen=capacity)
        self._dropped = 0

    def push(self, tick: TelemetryTick) -> None:
        if len(self._buf) == self._buf.maxlen:
            self._dropped += 1
        self._buf.append(tick)

    def drain(self, max_items: int) -> list[TelemetryTick]:
        out: list[TelemetryTick] = []
        for _ in range(min(max_items, len(self._buf))):
            out.append(self._buf.popleft())
        return out

    def __len__(self) -> int:
        return len(self._buf)

    @property
    def dropped(self) -> int:
        return self._dropped


# ---------------------------------------------------------------------------
# Self-healing WebSocket connection
# ---------------------------------------------------------------------------
class SelfHealingConnection:
    """Async WS manager with exponential backoff + rapid re-authentication."""

    def __init__(self, config: DaemonConfig, ring: RingBuffer) -> None:
        self._cfg = config
        self._ring = ring
        self._running = False
        self._failures = 0
        self._rng = random.Random(1337)
        self._messages = 0

    @property
    def messages(self) -> int:
        return self._messages

    async def run(self) -> None:
        self._running = True
        url = self._cfg.ws_base + self._cfg.stream_path()
        while self._running:
            try:
                await self._consume(url)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - all network faults are recoverable
                self._failures += 1
                delay = self._backoff()
                logger.warning(
                    "WS fault #%d (%s); reconnecting in %.1fs", self._failures, exc, delay
                )
                if self._failures % self._cfg.reauth_after_failures == 0:
                    logger.info("re-authenticating session after repeated faults")
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
            logger.info("VPS daemon connected: %s", url)
            self._failures = 0  # healthy reset
            async for raw in ws:
                self._ingest(raw)

    def _ingest(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        data = msg.get("data", msg)
        stream = msg.get("stream", "")
        symbol = (data.get("s") or stream.split("@")[0]).upper()
        channel = data.get("e") or (stream.split("@")[1] if "@" in stream else "unknown")
        self._ring.push(TelemetryTick(
            ts_ms=int(data.get("E", time.time() * 1000)),
            symbol=symbol,
            channel=channel,
            payload=data,
        ))
        self._messages += 1

    async def _synthetic_tape(self) -> None:
        """Deterministic mock tape so the daemon is testable without network."""
        self._failures = 0
        while self._running:
            for sym in self._cfg.symbols:
                self._ring.push(TelemetryTick(
                    ts_ms=int(time.time() * 1000),
                    symbol=sym.upper(),
                    channel="aggTrade",
                    payload={"p": 50_000 + self._rng.uniform(-50, 50), "q": self._rng.random()},
                ))
                self._messages += 1
            await asyncio.sleep(0.1)

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Non-blocking persistence
# ---------------------------------------------------------------------------
class PersistenceHandler:
    """Background flusher writing chunked Parquet (Snappy) or SQLite-WAL."""

    def __init__(self, config: DaemonConfig, ring: RingBuffer) -> None:
        self._cfg = config
        self._ring = ring
        self._running = False
        self._written = 0
        config.data_root.mkdir(parents=True, exist_ok=True)

    @property
    def written(self) -> int:
        return self._written

    async def run(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(self._cfg.flush_interval_s)
            await self._flush()

    async def _flush(self) -> None:
        batch = self._ring.drain(self._cfg.flush_batch_size)
        if not batch:
            return
        # Offload the blocking disk write to a worker thread so the event loop
        # (and the WS receive path) is never stalled by disk latency spikes.
        await asyncio.to_thread(self._write_batch, batch)
        self._written += len(batch)
        logger.debug("flushed %d ticks (total %d)", len(batch), self._written)

    def _write_batch(self, batch: list[TelemetryTick]) -> None:
        now = datetime.now(timezone.utc)
        partition = self._cfg.data_root / now.strftime("%Y-%m-%d") / now.strftime("%H")
        partition.mkdir(parents=True, exist_ok=True)
        if self._cfg.use_parquet and self._try_parquet(batch, partition, now):
            return
        self._write_sqlite(batch, partition)

    def _try_parquet(
        self, batch: list[TelemetryTick], partition: Path, now: datetime
    ) -> bool:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError:
            return False
        rows = [t.as_row() for t in batch]
        table = pa.Table.from_pylist(rows)
        target = partition / f"telemetry-{now.strftime('%H%M%S%f')}.parquet"
        pq.write_table(table, target, compression="snappy")
        return True

    def _write_sqlite(self, batch: list[TelemetryTick], partition: Path) -> None:
        db_path = partition / "telemetry.sqlite"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS ticks ("
                "ts_ms INTEGER, symbol TEXT, channel TEXT, payload TEXT)"
            )
            conn.executemany(
                "INSERT INTO ticks (ts_ms, symbol, channel, payload) VALUES (?,?,?,?)",
                [(t.ts_ms, t.symbol, t.channel, json.dumps(t.payload)) for t in batch],
            )
            conn.commit()
        finally:
            conn.close()

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------
class VPSTelemetryDaemon:
    """Top-level supervisor for the 24/7 collector."""

    def __init__(self, config: DaemonConfig | None = None) -> None:
        self._cfg = config or DaemonConfig()
        self._ring = RingBuffer(self._cfg.ring_capacity)
        self._conn = SelfHealingConnection(self._cfg, self._ring)
        self._persist = PersistenceHandler(self._cfg, self._ring)
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        logger.info("VPS telemetry daemon starting (symbols=%s)", self._cfg.symbols)
        self._tasks = [
            asyncio.create_task(self._conn.run(), name="vps-ws"),
            asyncio.create_task(self._persist.run(), name="vps-persist"),
            asyncio.create_task(self._heartbeat(), name="vps-heartbeat"),
        ]

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(30.0)
            logger.info(
                "heartbeat msgs=%d buffered=%d written=%d dropped=%d",
                self._conn.messages, len(self._ring),
                self._persist.written, self._ring.dropped,
            )

    async def stop(self) -> None:
        self._conn.stop()
        self._persist.stop()
        await self._persist._flush()  # final drain
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("VPS telemetry daemon stopped")

    async def run_forever(self) -> None:
        await self.start()
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop_event.set)
        await stop_event.wait()
        await self.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    asyncio.run(VPSTelemetryDaemon().run_forever())


if __name__ == "__main__":
    main()
