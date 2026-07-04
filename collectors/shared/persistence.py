"""ECONITH :: collectors.shared.persistence

Non-blocking, partition-aware Parquet snapshot writer.

Design goals:
  * Never block the asyncio receive loop: all disk IO is offloaded to a worker
    thread via ``asyncio.to_thread``.
  * Group buffered ticks by their partition key and append efficiently.
  * Prefer **Polars** for its fast, zero-copy Arrow writes; degrade to pandas,
    then to a stdlib JSONL fallback so the daemon runs on a bare VPS even before
    the data libraries are installed.

Append semantics: because Parquet has no cheap in-place append, each flush of a
partition writes a *new* rolling segment file (``<channel>_<hour>__<seq>.parquet``)
inside the partition directory. Downstream readers glob the directory, so many
small segments coalesce logically without a rewrite.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from collectors.shared.partitioning import PartitionKey, partition_path
from collectors.shared.schemas import CrossAssetTick

logger = logging.getLogger("econith.collectors.persistence")


def _polars() -> Optional[Any]:
    try:
        import polars as pl

        return pl
    except ImportError:
        return None


def _pandas() -> Optional[Any]:
    try:
        import pandas as pd

        return pd
    except ImportError:
        return None


class SnapshotWriter:
    """Buffers cross-asset ticks and flushes them to partitioned Parquet."""

    def __init__(
        self,
        root: Path | str = "datasets/raw",
        *,
        flush_threshold: int = 2_000,
    ) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._flush_threshold = max(1, flush_threshold)
        self._buffer: list[CrossAssetTick] = []
        self._written = 0
        # Rolling segment counter per partition file so appends never overwrite.
        self._seq: dict[Path, int] = defaultdict(int)
        self._backend = "polars" if _polars() else ("pandas" if _pandas() else "jsonl")
        logger.info("SnapshotWriter backend=%s root=%s", self._backend, self._root)

    @property
    def written(self) -> int:
        return self._written

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def add(self, tick: CrossAssetTick) -> bool:
        """Buffer a tick. Returns True when the flush threshold is reached."""
        self._buffer.append(tick)
        return len(self._buffer) >= self._flush_threshold

    async def flush(self) -> int:
        """Drain the buffer to disk without blocking the event loop."""
        if not self._buffer:
            return 0
        batch = self._buffer
        self._buffer = []
        return await asyncio.to_thread(self._write_batch, batch)

    # -- worker-thread body ---------------------------------------------------
    def _write_batch(self, batch: list[CrossAssetTick]) -> int:
        grouped: dict[Path, list[dict[str, Any]]] = defaultdict(list)
        for tick in batch:
            key = PartitionKey.from_tick(
                tick.ts_ms, tick.asset_class, tick.symbol, tick.channel
            )
            grouped[partition_path(self._root, key)].append(tick.as_row())

        total = 0
        for base_path, rows in grouped.items():
            base_path.parent.mkdir(parents=True, exist_ok=True)
            segment = self._next_segment(base_path)
            try:
                self._write_segment(segment, rows)
                total += len(rows)
            except Exception as exc:  # noqa: BLE001 - disk/codec fault must not kill loop
                logger.warning("segment write failed (%s); rows dropped: %d", exc, len(rows))
        self._written += total
        return total

    def _next_segment(self, base_path: Path) -> Path:
        seq = self._seq[base_path]
        self._seq[base_path] = seq + 1
        return base_path.with_name(f"{base_path.stem}__{seq:05d}{base_path.suffix}")

    def _write_segment(self, segment: Path, rows: list[dict[str, Any]]) -> None:
        if self._backend == "polars":
            pl = _polars()
            pl.DataFrame(rows).write_parquet(segment, compression="snappy")  # type: ignore[union-attr]
            return
        if self._backend == "pandas":
            pd = _pandas()
            pd.DataFrame(rows).to_parquet(  # type: ignore[union-attr]
                segment, engine="pyarrow", compression="snappy", index=False
            )
            return
        # stdlib fallback: newline-delimited JSON alongside the parquet name.
        jsonl = segment.with_suffix(".jsonl")
        with jsonl.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str) + "\n")

    async def close(self) -> int:
        """Final drain (call on shutdown)."""
        return await self.flush()
