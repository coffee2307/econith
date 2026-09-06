"""ECONITH :: infrastructure.storage.parquet

Parquet storage helper for the Feature Store (master plan, Phase 1, Step 4).
Raw tick/orderbook data is compressed to Parquet for low-latency columnar reads.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("econith.infra.storage.parquet")

DEFAULT_ROOT = Path("datasets/parquet")


class ParquetStore:
    """Append-friendly Parquet writer/reader (lazy pandas/pyarrow import)."""

    def __init__(self, root: Path | str = DEFAULT_ROOT) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, dataset: str) -> Path:
        return self.root / f"{dataset}.parquet"

    def write(self, dataset: str, rows: list[dict[str, Any]]) -> Path:
        import pandas as pd  # local import keeps Phase 0 import cost low

        path = self._path(dataset)
        df = pd.DataFrame(rows)
        df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
        logger.info("wrote %d rows -> %s", len(rows), path)
        return path

    def read(self, dataset: str) -> Any:
        import pandas as pd

        return pd.read_parquet(self._path(dataset), engine="pyarrow")
