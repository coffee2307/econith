"""CLI: export / inspect sealed World hypothesis rollouts (no training).

Usage:
    python -m training.export_world_rollouts --dir data/rollouts
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="List sealed World hypothesis rollout JSONL files (no train)."
    )
    parser.add_argument(
        "--dir",
        default="data/rollouts",
        help="Rollout directory (default: data/rollouts or ECONITH_ROLLOUT_DIR)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Show last N records from newest file",
    )
    args = parser.parse_args(argv)
    root = Path(args.dir)
    if not root.exists():
        print(f"no rollouts yet at {root}")
        return 0
    files = sorted(root.glob("world_hypotheses_*.jsonl"))
    if not files:
        print(f"no world_hypotheses_*.jsonl under {root}")
        return 0
    newest = files[-1]
    print(f"files={len(files)} newest={newest}")
    lines = newest.read_text(encoding="utf-8").strip().splitlines()
    for line in lines[-max(1, args.limit) :]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            print(line[:120])
            continue
        print(
            f"  {row.get('ts')} id={row.get('hypothesis_id')} "
            f"deltas={len(row.get('deltas') or {})} "
            f"prompt={str(row.get('prompt') or '')[:80]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
