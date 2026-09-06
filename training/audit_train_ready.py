"""One-shot readiness audit for RunPod H200 training.

Usage (from repo root)::

    python -m training.audit_train_ready
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pandas as pd

from ai.agents.agent_loaders import PPO_FEATURE_COLS
from training.train_ppo import (
    AGENT_PROFILES,
    DATASET_PROFILES,
    _resolve_realized_col,
)


def _shards(p: Path) -> int:
    return sum(1 for _ in p.rglob("*.parquet")) if p.exists() else 0


def main() -> int:
    print("=== LAKE (raw — do NOT upload 48GB ticks to RunPod) ===")
    lake = _ROOT / "datasets" / "training_lake"
    for rel in [
        "market/klines_hist",
        "macro/fred",
        "tradfi/history",
        "alt/futures",
        "market/crypto_majors",
    ]:
        p = lake / rel
        print(f"  {rel}: exists={p.exists()} shards={_shards(p)}")

    print("\n=== TRAIN ARTIFACTS (upload these) ===")
    hard_fail = False
    warnings: list[str] = []

    for name, prof in DATASET_PROFILES.items():
        labeled = _ROOT / Path(prof["data"])
        holdout = _ROOT / Path(prof["holdout"])
        for kind, path in (("labeled", labeled), ("holdout", holdout)):
            if not path.exists():
                print(f"  [{name}] {kind}: MISSING -> {path}")
                hard_fail = True
            else:
                print(f"  [{name}] {kind}: {path.stat().st_size / 1e6:.1f} MB")

        if not labeled.exists():
            continue
        df = pd.read_parquet(labeled)
        miss = [c for c in PPO_FEATURE_COLS if c not in df.columns]
        need_miss = [c for c in ("ts_ms", "symbol", "reward") if c not in df.columns]
        print(f"  [{name}] rows={len(df):,} symbols={df['symbol'].nunique()}")
        if need_miss:
            print(f"  [{name}] CORE MISSING: {need_miss}")
            hard_fail = True
        if miss:
            # klines track intentionally lacks tick microstructure cols
            print(f"  [{name}] optional PPO cols absent (OK if intersection non-empty): {miss}")
            warnings.append(f"{name}: missing optional {miss}")
        present = [c for c in PPO_FEATURE_COLS if c in df.columns]
        if len(present) < 5:
            print(f"  [{name}] too few feature cols ({len(present)})")
            hard_fail = True
        for agent in AGENT_PROFILES:
            try:
                col = _resolve_realized_col(df, agent, name)
                print(f"  [{name}] agent={agent} -> target={col}")
            except SystemExit as exc:
                print(f"  [{name}] agent={agent} FAIL: {exc}")
                hard_fail = True
        if len(df) < 10_000:
            warnings.append(f"{name}: only {len(df)} rows — thin for serious train")

    print("\n=== VERDICT ===")
    for w in warnings:
        print(f"  WARN: {w}")
    if hard_fail:
        print("NOT_READY — fix missing labeled/holdout or core columns before RunPod")
        return 1
    print("READY — upload processed parquets + code; train with --dataset-profile klines|hf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
