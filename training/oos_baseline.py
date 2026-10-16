"""Causal, no-training benchmark for timestamped crypto holdout evaluation.

This is NOT an ML model or a profitable strategy claim. It writes signals from
strictly lagged market prices so the deployment gate can test a genuine baseline
rather than read realized forward labels as an oracle.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


def generate_causal_baseline(frame: pd.DataFrame, lookback: int = 12) -> pd.DataFrame:
    """Predict future return sign using only lagged historical prices.

    Observation at row t uses close prices through t-1 at the latest; this
    remains causal even if ts_ms is the OPEN timestamp of bar t.
    """
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    necessary = {"ts_ms", "price", "symbol"}
    missing = necessary.difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns: {sorted(missing)}")

    frames = []
    for symbol, group in frame.groupby("symbol", sort=True):
        g = group.copy()
        ts = pd.to_numeric(g["ts_ms"], errors="coerce")
        price = pd.to_numeric(g["price"], errors="coerce")
        if ts.isna().any() or not ts.is_monotonic_increasing or ts.duplicated().any():
            raise ValueError(f"{symbol}: timestamps not strictly increasing")
        if not np.isfinite(price.to_numpy(dtype="float64")).all() or (price <= 0).any():
            raise ValueError(f"{symbol}: invalid price")

        past_close = price.shift(1)
        start_close = price.shift(lookback + 1)
        momentum = (past_close / start_close - 1.0) / lookback

        horizon_cols = [c for c in g.columns if c.startswith("forward_return_")]
        if not horizon_cols:
            raise ValueError("no forward_return_* label in input")
        for label in horizon_cols:
            horizon = label.removeprefix("forward_return_")
            g[f"oos_prediction_{horizon}"] = momentum.clip(-0.05, 0.05).fillna(0.0)

        g["prediction_asof_ms"] = ts.shift(1).fillna(ts).astype("int64")
        g["prediction_model_id"] = f"rule-lagged-momentum-v1-lookback{lookback}"
        frames.append(g)

    if not frames:
        raise ValueError("empty input")
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values(["ts_ms", "symbol"])
        .reset_index(drop=True)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create strictly lagged OOS benchmark signals")
    parser.add_argument("--input", required=True, type=Path, help="labeled holdout Parquet")
    parser.add_argument("--output", required=True, type=Path, help="new signal Parquet")
    parser.add_argument("--lookback", type=int, default=12)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("output must differ from input: preserve the original holdout")
    output = generate_causal_baseline(pd.read_parquet(args.input), args.lookback)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(args.output, index=False)
    print(f"Wrote {len(output)} causal baseline rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
