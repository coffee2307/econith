"""ECONITH :: training.label  (PHASE B -- Labeling / The Refinery)

Grade the raw ore into training-ready material.

Economic analogy
----------------
Phase A mined thousands of identical-looking crates of market data. But raw data
has no *lesson* attached -- it doesn't say "buying here was smart" or "this was a
trap". The refinery walks the history with the benefit of hindsight and stamps
each crate with two things:

  1. **Forward returns** (1m / 5m / 15m) -- "what actually happened to the price
     shortly after this moment?" This is the answer key the apprentices study.

  2. **An anti-greed reward** -- computed with the SAME ``breakdown_reward`` the
     live system uses, so the classroom grading matches the real exam. It rewards
     steady gains and *punishes* deep drawdowns and downside volatility, so models
     never learn the reckless "bet the farm" behaviour that blows up accounts.

Finally it splits the warehouse **chronologically** (never shuffled): the first
80% is the textbook (``quant_labeled.parquet``); the most recent 20% is a sealed
final exam (``quant_holdout.parquet``) the models never see during training. This
time-ordered split is sacred -- shuffling would let a model "peek at the future",
which looks great in the lab and loses money in reality.

Run it:
    python training/label.py --input ./datasets/features \
        --output ./datasets/processed/quant_labeled.parquet --holdout-ratio 0.20
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import deque
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai.reward.reward import RewardConfig, breakdown_reward  # noqa: E402
from infrastructure.feature_store.loader import FeatureLoader  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.label")

# Forward-return horizons in milliseconds (the collector stamps ts_ms per row).
HORIZONS_MS = {
    "forward_return_1m": 60_000,
    "forward_return_5m": 300_000,
    "forward_return_15m": 900_000,
}

# Rolling window (in rows) used for the Sortino downside + drawdown context.
REWARD_WINDOW = 64


def _reference_price(df) -> "np.ndarray":
    """Best available fair price per row: trade ``price``, else order-book ``mid``."""
    import pandas as pd

    price = pd.to_numeric(df.get("price"), errors="coerce")
    mid = pd.to_numeric(df.get("mid"), errors="coerce")
    return price.fillna(mid).to_numpy(dtype="float64")


def _forward_returns(prices: "np.ndarray", ts_ms: "np.ndarray") -> dict[str, np.ndarray]:
    """Vectorised forward returns via timestamp search (robust to variable cadence).

    For each row at time ``t`` we jump to the FIRST row at or after ``t + horizon``
    and measure the price change. Using timestamps (not a fixed row offset) means
    it stays correct even if the market's data rate speeds up or slows down.
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
    """Per-row shaped reward using the production ``breakdown_reward``.

    We build a synthetic equity curve from the realised 1-minute returns, track
    the running peak-to-trough drawdown, and keep a rolling window of recent
    returns for the downside (Sortino) term -- then hand each row to the exact
    same reward function the live trader is graded by. Turnover/concentration are
    zero here because labeling has no live position book yet; the training envs
    (train_ppo) add those penalties when a policy actually trades.
    """
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


def label_dataset(input_dir: str, output_path: str, holdout_ratio: float) -> dict:
    """Refine the feature store into labeled + holdout Parquet files."""
    import pandas as pd

    loader = FeatureLoader(root=input_dir)
    df = loader.load("features")
    if df is None or len(df) == 0:
        raise SystemExit(
            f"no feature partitions found in {input_dir} -- run `make data-collect` first"
        )

    logger.info("loaded %d raw feature rows from %s", len(df), input_dir)

    # 1) Restore chronological order -- the arrow of time must point forward.
    if "ts_ms" not in df.columns:
        raise SystemExit("feature rows lack 'ts_ms'; re-collect with the current collector")
    df = df.sort_values("ts_ms").reset_index(drop=True)
    ts_ms = df["ts_ms"].to_numpy(dtype="int64")
    prices = _reference_price(df)

    # 2) Stamp forward returns (the answer key).
    fwd = _forward_returns(prices, ts_ms)
    for name, arr in fwd.items():
        df[name] = arr

    # 3) Grade each row with the anti-greed reward, using 1m return as the step.
    step = np.nan_to_num(fwd["forward_return_1m"], nan=0.0)
    df["reward"] = _anti_greed_reward(step, RewardConfig())

    # 4) Drop the tail rows whose full 15m future doesn't exist yet (no answer key).
    before = len(df)
    df = df[np.isfinite(df["forward_return_1m"].to_numpy())].reset_index(drop=True)
    logger.info("kept %d/%d rows with a valid 1m forward window", len(df), before)
    if len(df) < 10:
        raise SystemExit(
            "not enough rows with a forward window -- collect a longer session "
            "(need at least ~15+ minutes of data for the 15m horizon)"
        )

    # 5) Chronological split -- NEVER shuffle (prevents look-ahead leakage).
    split = int(len(df) * (1.0 - holdout_ratio))
    train_df = df.iloc[:split].reset_index(drop=True)
    holdout_df = df.iloc[split:].reset_index(drop=True)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    holdout_path = out.parent / "quant_holdout.parquet"

    train_df.to_parquet(out, engine="pyarrow", compression="snappy", index=False)
    holdout_df.to_parquet(holdout_path, engine="pyarrow", compression="snappy", index=False)

    summary = {
        "rows_total": int(len(df)),
        "rows_train": int(len(train_df)),
        "rows_holdout": int(len(holdout_df)),
        "labeled_path": str(out),
        "holdout_path": str(holdout_path),
        "reward_mean": float(np.nanmean(df["reward"].to_numpy())),
        "fwd1m_mean": float(np.nanmean(df["forward_return_1m"].to_numpy())),
    }
    logger.info(
        "refined -> %s (%d train) + %s (%d holdout) | reward_mean=%.5f",
        out, summary["rows_train"], holdout_path, summary["rows_holdout"],
        summary["reward_mean"],
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="label.py",
        description="ECONITH Phase B -- label features with forward returns + reward.",
    )
    p.add_argument("--input", default="./datasets/features", help="feature store dir")
    p.add_argument(
        "--output",
        default="./datasets/processed/quant_labeled.parquet",
        help="labeled output (holdout is written next to it)",
    )
    p.add_argument(
        "--holdout-ratio", type=float, default=0.20,
        help="fraction of the most-recent data sealed as the final exam",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Delegate to multi-symbol-safe labeler (avoids cross-asset contamination)."""
    from training.quant.label_symbol import build_parser as _sym_parser
    from training.quant.label_symbol import label_dataset

    logger.info("label.py delegates to training.quant.label_symbol (per-symbol safe)")
    args = _sym_parser().parse_args(argv)
    if not (0.0 < args.holdout_ratio < 0.9):
        raise SystemExit("--holdout-ratio must be between 0 and 0.9")
    label_dataset(args.input, args.output, args.holdout_ratio)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
