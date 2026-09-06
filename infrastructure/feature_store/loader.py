"""ECONITH :: infrastructure.feature_store.loader

Reads feature partitions back from the Parquet Feature Store
(master plan, Phase 1, Step 4). Used by training / backtest pipelines.

Pandas / pyarrow are imported lazily so importing this module is cheap.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("econith.feature_store.loader")


class FeatureLoader:
    """Loads and concatenates Parquet feature partitions."""

    def __init__(self, root: Path | str = "datasets/features") -> None:
        self._root = Path(root)

    def list_partitions(self, dataset: str = "features") -> list[Path]:
        if not self._root.exists():
            return []
        return sorted(self._root.glob(f"{dataset}_*.parquet"))

    def load(self, dataset: str = "features") -> Any:
        """Return a concatenated DataFrame across all partitions (or None)."""
        import pandas as pd  # lazy heavy import

        parts = self.list_partitions(dataset)
        if not parts:
            logger.info("no feature partitions for dataset '%s'", dataset)
            return None
        frames = [pd.read_parquet(p, engine="pyarrow") for p in parts]
        return pd.concat(frames, ignore_index=True)
