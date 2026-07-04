"""ECONITH :: collectors.shared

Cross-collector primitives shared by every ingestion daemon. No network calls,
no ML, no project-runtime imports — pure data contracts + storage plumbing.
"""
from __future__ import annotations

from collectors.shared.partitioning import (
    PartitionKey,
    partition_path,
    resolve_asset_class,
    resolve_desk,
)
from collectors.shared.persistence import SnapshotWriter
from collectors.shared.schemas import (
    ASSET_CLASSES,
    AssetClass,
    CrossAssetTick,
    validate_tick,
)

__all__ = [
    "AssetClass",
    "ASSET_CLASSES",
    "CrossAssetTick",
    "validate_tick",
    "PartitionKey",
    "partition_path",
    "resolve_asset_class",
    "resolve_desk",
    "SnapshotWriter",
]
