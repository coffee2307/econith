"""Promote collector raw partitions into the Feature Store layout.

Copies/flattens ``datasets/raw/**/*.parquet`` (and jsonl market ticks when
present) into ``datasets/features/features_*.parquet`` so ``FeatureLoader`` and
``training/label.py`` see a single train-ready root.

Run:
    python -m training.prepare_feature_store --raw ./datasets/raw --out ./datasets/features
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.prepare_feature_store")


def prepare(raw_root: Path, out_root: Path) -> dict[str, int]:
    out_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    copied = 0
    for src in sorted(raw_root.rglob("*.parquet")):
        dst = out_root / f"features_{stamp}_{copied:04d}.parquet"
        shutil.copy2(src, dst)
        copied += 1
        logger.info("promoted %s -> %s", src, dst.name)
    if copied == 0:
        logger.warning("no parquet under %s — feature store unchanged", raw_root)
    return {"promoted": copied, "out": str(out_root)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Raw collector → feature store cut-over")
    p.add_argument("--raw", default="./datasets/raw")
    p.add_argument("--out", default="./datasets/features")
    args = p.parse_args(argv)
    summary = prepare(Path(args.raw), Path(args.out))
    logger.info("done: %s", summary)
    return 0 if summary["promoted"] >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
