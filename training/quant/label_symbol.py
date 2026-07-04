"""ECONITH :: training.quant.label_symbol  (PHASE B -- multi-coin-safe labeling)

Rewrite of the legacy ``training/label.py`` that fixes a critical
cross-contamination bug.

The critical bug
----------------
The old pipeline computed forward returns on a SINGLE global timeline::

    df = df.sort_values("ts_ms").reset_index(drop=True)   # BUG: mixes symbols
    prices = _reference_price(df)
    fwd = _forward_returns(prices, ts_ms)

When a dataset contains more than one symbol (BTCUSDT, ETHUSDT, DOGEUSDT, ...),
that global sort interleaves rows from different assets. The forward-return
search then jumps from, say, a BTC row at ``t`` to a DOGE row at ``t + 1m`` and
computes a nonsensical ``DOGE_price / BTC_price - 1`` return. Rewards are then
graded on this garbage, poisoning every downstream model.

The fix
-------
Everything is computed **within** ``groupby("symbol")``: each asset gets its own
chronologically-sorted timeline, its own forward-return search, and its own
anti-greed reward equity curve. Only after per-symbol labeling do we optionally
re-assemble a combined frame, and the train/holdout split is done **per symbol**
so both partitions contain every asset in correct chronological order.
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import deque
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai.reward.reward import RewardConfig, breakdown_reward  # noqa: E402
from infrastructure.feature_store.loader import FeatureLoader  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.quant.label_symbol")

HORIZONS_MS = {
    "forward_return_1m": 60_000,
    "forward_return_5m": 300_000,
    "forward_return_15m": 900_000,
}
REWARD_WINDOW = 64
_MIN_ROWS_PER_SYMBOL = 10


def _reference_price(df) -> "np.ndarray":
    """Best available fair price per row: trade ``price``, else order-book ``mid``."""
    import pandas as pd

    price = pd.to_numeric(df.get("price"), errors="coerce")
    mid = pd.to_numeric(df.get("mid"), errors="coerce")
    return price.fillna(mid).to_numpy(dtype="float64")


def _forward_returns(prices: "np.ndarray", ts_ms: "np.ndarray") -> dict[str, np.ndarray]:
    """Vectorised forward returns via timestamp search, on ONE symbol's timeline.

    ``ts_ms`` MUST be sorted ascending and belong to a single asset. For each row
    at time ``t`` we jump to the first row at or after ``t + horizon`` and measure
    the price change; timestamp search keeps it correct under variable cadence.
    """
    out: dict[str, np.ndarray] = {}
    n = len(ts_ms)
    for name, h in HORIZONS_MS.items():
        target = ts_ms + h
        idx = np.searchsorted(ts_ms, target, side="left")
        fr = np.full(n, np.nan, dtype="float64")
        valid = idx < n
        src = prices[valid]
        dst = prices[idx[valid]]
        with np.errstate(divide="ignore", invalid="ignore"):
            ret = np.where(src > 0, dst / src - 1.0, np.nan)
        fr[valid] = ret
        out[name] = fr
    return out


def _anti_greed_reward(step_returns: "np.ndarray", cfg: RewardConfig) -> np.ndarray:
    """Per-row shaped reward on ONE symbol's return stream (own equity curve)."""
    n = len(step_returns)
    rewards = np.zeros(n, dtype="float64")
    equity = 1.0
    peak = 1.0
    window: deque[float] = deque(maxlen=REWARD_WINDOW)
    for i in range(n):
        r = float(step_returns[i]) if np.isfinite(step_returns[i]) else 0.0
        equity *= 1.0 + r
        peak = max(peak, equity)
        drawdown = 0.0 if peak <= 0 else max(0.0, 1.0 - equity / peak)
        window.append(r)
        rewards[i] = breakdown_reward(
            step_return=r,
            max_drawdown=drawdown,
            equity_returns=list(window),
            turnover=0.0,
            position_concentration=0.0,
            config=cfg,
        ).reward
    return rewards


def _label_one_symbol(sym_df, cfg: RewardConfig):
    """Label a single symbol's frame in strict chronological isolation.

    Returns the labeled frame (rows lacking a 1m forward window dropped) or None
    if the symbol has too little history to carry a valid forward window.
    """
    sym_df = sym_df.sort_values("ts_ms").reset_index(drop=True)
    ts_ms = sym_df["ts_ms"].to_numpy(dtype="int64")
    prices = _reference_price(sym_df)

    fwd = _forward_returns(prices, ts_ms)
    for name, arr in fwd.items():
        sym_df[name] = arr

    step = np.nan_to_num(fwd["forward_return_1m"], nan=0.0)
    sym_df["reward"] = _anti_greed_reward(step, cfg)

    valid = np.isfinite(sym_df["forward_return_1m"].to_numpy())
    sym_df = sym_df[valid].reset_index(drop=True)
    if len(sym_df) < _MIN_ROWS_PER_SYMBOL:
        return None
    return sym_df


def label_dataset(input_dir: str, output_path: str, holdout_ratio: float) -> dict:
    """Refine the feature store into labeled + holdout Parquet, per-symbol safe."""
    import pandas as pd

    loader = FeatureLoader(root=input_dir)
    df = loader.load("features")
    if df is None or len(df) == 0:
        raise SystemExit(
            f"no feature partitions found in {input_dir} -- run data collection first"
        )
    logger.info("loaded %d raw feature rows from %s", len(df), input_dir)

    if "ts_ms" not in df.columns:
        raise SystemExit("feature rows lack 'ts_ms'; re-collect with the current collector")

    # A dataset with no symbol column is a legacy single-asset capture. Treat it
    # as one implicit symbol so the group-by path stays uniform.
    if "symbol" not in df.columns:
        logger.warning("no 'symbol' column -- treating dataset as a single asset")
        df["symbol"] = "UNKNOWN"

    cfg = RewardConfig()
    train_parts: list = []
    holdout_parts: list = []
    per_symbol_stats: dict[str, int] = {}

    # CRITICAL FIX: isolate every asset. No global cross-symbol sort ever runs.
    for symbol, group in df.groupby("symbol", sort=True):
        labeled = _label_one_symbol(group.copy(), cfg)
        if labeled is None:
            logger.warning("skipping %s -- insufficient forward-window history", symbol)
            continue
        # Per-symbol chronological split (never shuffle -> no look-ahead leak).
        split = int(len(labeled) * (1.0 - holdout_ratio))
        train_parts.append(labeled.iloc[:split])
        holdout_parts.append(labeled.iloc[split:])
        per_symbol_stats[str(symbol)] = len(labeled)
        logger.info("labeled %s: %d rows (%d train / %d holdout)",
                    symbol, len(labeled), split, len(labeled) - split)

    if not train_parts:
        raise SystemExit(
            "no symbol had enough forward-window history -- collect longer sessions"
        )

    # Re-assemble. Global sort here is SAFE: labels are already computed per
    # symbol; the combined frame is only for storage/shuffled-batch training.
    train_df = pd.concat(train_parts, ignore_index=True).sort_values("ts_ms").reset_index(drop=True)
    holdout_df = pd.concat(holdout_parts, ignore_index=True).sort_values("ts_ms").reset_index(drop=True)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    holdout_path = out.parent / "quant_holdout.parquet"
    train_df.to_parquet(out, engine="pyarrow", compression="snappy", index=False)
    holdout_df.to_parquet(holdout_path, engine="pyarrow", compression="snappy", index=False)

    summary = {
        "symbols": per_symbol_stats,
        "train_rows": len(train_df),
        "holdout_rows": len(holdout_df),
        "labeled_output": str(out),
        "holdout_output": str(holdout_path),
        "fwd1m_mean": float(np.nanmean(train_df["forward_return_1m"].to_numpy()))
        if len(train_df) else 0.0,
    }
    logger.info(
        "labeling complete: %d symbols, %d train / %d holdout rows",
        len(per_symbol_stats), summary["train_rows"], summary["holdout_rows"],
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="label_symbol.py",
        description="ECONITH multi-coin-safe labeler (per-symbol forward returns)",
    )
    p.add_argument("--input", default="./datasets/features", help="raw feature dir")
    p.add_argument(
        "--output",
        default="./datasets/processed/quant_labeled.parquet",
        help="labeled output parquet",
    )
    p.add_argument("--holdout-ratio", type=float, default=0.20)
    return p


def main() -> None:
    args = build_parser().parse_args()
    label_dataset(args.input, args.output, args.holdout_ratio)


if __name__ == "__main__":
    main()
