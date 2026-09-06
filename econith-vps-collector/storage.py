"""ECONITH VPS Collector :: storage

Buffer management and append-only Parquet writer.

Design contract
---------------
* **Zero-copy buffering** via Polars: records accumulate in a plain Python list
  (allocation-light) and are materialised into a Polars ``DataFrame`` only at
  flush time, then written straight to Arrow/Parquet.
* **Never block the event loop**: all disk IO is offloaded to a worker thread
  through ``asyncio.to_thread`` so the websocket receive loop never stalls.
* **Append-only, non-overwriting**: Parquet has no cheap in-place append, so each
  flush of a partition writes a *new* rolling segment file with a unique,
  monotonically incrementing sequence suffix::

      <channel>_<hour>__<seq>.parquet

  The sequence counter is seeded by scanning existing segments on first touch,
  so a daemon restart never clobbers data already on disk.

Canonical partition layout (raw lake)::

    datasets/raw/market/<desk>/<symbol>/<YYYY-MM-DD>/<channel>_<hour>__<seq>.parquet
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from config import ASSET_CLASS, SOURCE, resolve_desk

logger = logging.getLogger("econith.vps.storage")

# Matches the numeric segment suffix so we can resume after the highest one.
_SEGMENT_RE = re.compile(r"__(\d+)\.parquet$")


@dataclass(slots=True)
class MarketRecord:
    """One normalised market observation destined for cold storage.

    Fields:
        ts_ms:   absolute UTC epoch milliseconds (universal join key)
        symbol:  instrument identifier, uppercased (e.g. BTCUSDT)
        channel: stream/event sub-type (e.g. aggTrade, depthUpdate, markPriceUpdate)
        value:   primary scalar (price/mark); ``None`` for pure book frames
        payload: the raw normalised field map, kept for lossless replay
    """

    ts_ms: int
    symbol: str
    channel: str
    value: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        """Flatten into a columnar-friendly dict (payload JSON-encoded)."""
        return {
            "ts_ms": self.ts_ms,
            "asset_class": ASSET_CLASS,
            "symbol": self.symbol,
            "channel": self.channel,
            "source": SOURCE,
            "value": self.value,
            "payload": json.dumps(self.payload, separators=(",", ":"), default=str),
        }


@dataclass(slots=True, frozen=True)
class _PartitionKey:
    """Uniquely identifies a partition segment family on disk."""

    desk: str
    symbol: str
    date: str   # YYYY-MM-DD (UTC)
    hour: str   # HH (UTC)
    channel: str

    @classmethod
    def from_record(cls, rec: MarketRecord) -> "_PartitionKey":
        dt = datetime.fromtimestamp(rec.ts_ms / 1000.0, tz=timezone.utc)
        return cls(
            desk=resolve_desk(rec.symbol),
            symbol=rec.symbol.upper(),
            date=dt.strftime("%Y-%m-%d"),
            hour=dt.strftime("%H"),
            channel=rec.channel,
        )


class ParquetSnapshotWriter:
    """Buffers market records and flushes them to partitioned Parquet segments."""

    def __init__(
        self,
        root: Path | str = "datasets/raw",
        *,
        flush_threshold: int = 2_000,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._flush_threshold = max(1, flush_threshold)
        self._buffer: list[MarketRecord] = []
        self._written = 0
        # Per-partition rolling segment counter (lazily seeded from disk).
        self._seq: dict[Path, int] = {}
        logger.info(
            "ParquetSnapshotWriter ready (backend=polars root=%s threshold=%d)",
            self._root, self._flush_threshold,
        )

    @property
    def written(self) -> int:
        return self._written

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def add(self, record: MarketRecord) -> bool:
        """Buffer a record. Returns True once the flush threshold is reached."""
        self._buffer.append(record)
        return len(self._buffer) >= self._flush_threshold

    async def flush(self) -> int:
        """Drain the current buffer to disk without blocking the event loop.

        The buffer is swapped out atomically before handing the batch to a
        worker thread, so ingestion can keep appending during the write.
        """
        if not self._buffer:
            return 0
        batch = self._buffer
        self._buffer = []
        try:
            return await asyncio.to_thread(self._write_batch, batch)
        except Exception as exc:  # noqa: BLE001 - never let a flush kill the daemon
            logger.error("flush failed, %d records lost: %s", len(batch), exc)
            return 0

    async def close(self) -> int:
        """Final synchronous drain -- call on graceful shutdown."""
        return await self.flush()

    # -- worker-thread body ---------------------------------------------------
    def _write_batch(self, batch: list[MarketRecord]) -> int:
        grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        for rec in batch:
            base = self._partition_base(_PartitionKey.from_record(rec))
            grouped[base].append(rec.as_row())

        total = 0
        for base_path, rows in grouped.items():
            base_path.parent.mkdir(parents=True, exist_ok=True)
            segment = self._next_segment(base_path)
            try:
                pl.DataFrame(rows).write_parquet(segment, compression="snappy")
                total += len(rows)
            except Exception as exc:  # noqa: BLE001 - disk/codec fault, skip this segment
                logger.warning(
                    "segment write failed (%s); %d rows dropped for %s",
                    exc, len(rows), segment.name,
                )
        self._written += total
        return total

    def _partition_base(self, key: _PartitionKey) -> Path:
        """Resolve the segment *family* base path (before the ``__<seq>`` suffix).

        Layout: ``<root>/market/<desk>/<symbol>/<date>/<channel>_<hour>.parquet``
        """
        return (
            self._root
            / ASSET_CLASS
            / key.desk
            / key.symbol
            / key.date
            / f"{key.channel}_{key.hour}.parquet"
        )

    def _next_segment(self, base_path: Path) -> Path:
        """Return the next non-overwriting segment path for a partition family.

        The counter is seeded from disk on first touch so restarts continue past
        any segments already written for the current hour.
        """
        if base_path not in self._seq:
            self._seq[base_path] = self._scan_next_seq(base_path)
        seq = self._seq[base_path]
        self._seq[base_path] = seq + 1
        return base_path.with_name(f"{base_path.stem}__{seq:05d}{base_path.suffix}")

    @staticmethod
    def _scan_next_seq(base_path: Path) -> int:
        """Find the next free sequence index by inspecting existing segments."""
        parent = base_path.parent
        if not parent.exists():
            return 0
        highest = -1
        pattern = f"{base_path.stem}__*{base_path.suffix}"
        for existing in parent.glob(pattern):
            m = _SEGMENT_RE.search(existing.name)
            if m:
                highest = max(highest, int(m.group(1)))
        return highest + 1
