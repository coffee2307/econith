"""ECONITH :: infrastructure.feature_store.writer

Buffered Parquet writer for feature rows (master plan, Phase 1, Step 4).

Rows are accumulated in memory and flushed to Parquet (snappy-compressed) once
the batch threshold is reached, keeping the event-loop hot path allocation-light.
Pandas / pyarrow are imported lazily inside ``flush`` so importing this module
never pulls in the heavy data stack.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from infrastructure.storage.parquet import ParquetStore

logger = logging.getLogger("econith.feature_store.writer")


class FeatureWriter:
    """Batches feature rows and flushes them to the Parquet Feature Store."""

    def __init__(
        self,
        dataset: str = "features",
        batch_size: int = 500,
        root: Path | str = "datasets/features",
    ) -> None:
        self._store = ParquetStore(root=root)
        self._dataset = dataset
        self._batch_size = batch_size
        self._buffer: list[dict[str, Any]] = []
        self._written = 0
        self._partition = 0

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    @property
    def total_written(self) -> int:
        return self._written

    def add(self, row: dict[str, Any]) -> None:
        self._buffer.append(row)
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        # Distinct partition file per flush -- the underlying ParquetStore
        # truncates per dataset name, so we never overwrite earlier batches.
        partition = f"{self._dataset}_{self._partition:05d}"
        try:
            self._store.write(partition, self._buffer)
            self._written += len(self._buffer)
            self._partition += 1
        except Exception as exc:  # noqa: BLE001 -- never crash the loop on disk/codec errors
            logger.warning("feature flush failed (%s); keeping buffer", exc)
            return
        self._buffer.clear()
