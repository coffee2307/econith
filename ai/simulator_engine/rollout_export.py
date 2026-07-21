"""ECONITH :: ai.simulator_engine.rollout_export

Sealed JSONL writer for World hypothesis cycles → later H200 PPO datasets.

Does not train, does not write active.yaml.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai.simulator_engine.hypothesis_schema import HypothesisOutcome

logger = logging.getLogger("econith.world.rollout_export")


def _default_root() -> Path:
    raw = (os.getenv("ECONITH_ROLLOUT_DIR") or "data/rollouts").strip()
    return Path(raw)


class SealedRolloutWriter:
    """Append-only sealed JSONL under ``data/rollouts/`` (or ``ECONITH_ROLLOUT_DIR``)."""

    def __init__(self, root: Path | str | None = None) -> None:
        self._root = Path(root) if root is not None else _default_root()

    @property
    def root(self) -> Path:
        return self._root

    def write(
        self,
        *,
        hypothesis_id: str,
        prompt: str,
        outcome: HypothesisOutcome,
        features: dict[str, Any] | None = None,
        world_coupling: float = 0.0,
        regime: str = "",
        action: str = "",
        signal: dict[str, Any] | None = None,
    ) -> Path | None:
        if outcome.status != "ok":
            return None
        self._root.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = self._root / f"world_hypotheses_{day}.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sealed": True,
            "hypothesis_id": hypothesis_id,
            "prompt": prompt,
            "status": outcome.status,
            "tick_span": outcome.tick_span,
            "deltas": outcome.deltas,
            "micro_summary": outcome.micro_summary,
            "features": features or {},
            "world_coupling": world_coupling,
            "regime": regime,
            "action": action,
            "signal": signal or {},
            "pre_macro": outcome.pre_macro,
            "post_macro": outcome.post_macro,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        logger.debug("sealed rollout appended -> %s", path)
        return path
