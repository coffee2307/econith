"""Checkpoint format detection for SB3 ``.zip`` vs H200 ``.pt`` artifacts.

Trading desks (trend / mean_reversion / scalper) require stable-baselines3
``.zip`` archives. World neural reaction models expect torch ``.pt`` state-dicts
produced by the H200 harness. Mixing the two silently is a common footgun —
this module makes the mismatch explicit.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path


class CheckpointKind(str, Enum):
    SB3_ZIP = "sb3_zip"
    TORCH_PT = "torch_pt"
    MISSING = "missing"
    UNKNOWN = "unknown"


def classify_checkpoint(path: str | Path) -> CheckpointKind:
    """Return the artifact kind for a checkpoint path (existence + suffix)."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return CheckpointKind.MISSING
    suffix = p.suffix.lower()
    if suffix == ".zip":
        return CheckpointKind.SB3_ZIP
    if suffix in (".pt", ".pth"):
        return CheckpointKind.TORCH_PT
    return CheckpointKind.UNKNOWN
