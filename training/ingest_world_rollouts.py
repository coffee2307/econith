"""Convert sealed World hypothesis JSONL rollouts into a train-ready parquet.

Does not train or write active.yaml — only prepares features for PPO later.

Run:
    python -m training.ingest_world_rollouts --dir data/rollouts \\
        --out datasets/processed/world_rollouts.parquet
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.ingest_world_rollouts")


def ingest(rollout_dir: Path, out_path: Path) -> dict[str, int]:
    rows: list[dict] = []
    for path in sorted(rollout_dir.glob("world_hypotheses_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            feats = row.get("features") or {}
            flat = {
                "ts": row.get("ts"),
                "hypothesis_id": row.get("hypothesis_id"),
                "world_coupling": float(row.get("world_coupling") or 0.0),
                "regime": row.get("regime") or "",
                "action": row.get("action") or "",
                "delta_count": len(row.get("deltas") or {}),
            }
            if isinstance(feats, dict):
                for k, v in feats.items():
                    if isinstance(v, (int, float)):
                        flat[f"f_{k}"] = float(v)
            rows.append(flat)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        logger.warning("no rollout rows under %s", rollout_dir)
        return {"rows": 0}
    try:
        import pandas as pd

        pd.DataFrame(rows).to_parquet(out_path, index=False)
    except Exception:
        # Fallback without pyarrow
        out_path = out_path.with_suffix(".jsonl")
        with out_path.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    logger.info("wrote %d rollout rows -> %s", len(rows), out_path)
    return {"rows": len(rows), "out": str(out_path)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="World sealed rollouts → parquet")
    p.add_argument("--dir", default="data/rollouts")
    p.add_argument("--out", default="datasets/processed/world_rollouts.parquet")
    args = p.parse_args(argv)
    ingest(Path(args.dir), Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
