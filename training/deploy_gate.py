"""ECONITH :: training.deploy_gate

Mandatory pre-promote backtest gate. Deploy refuses to write active.yaml when
holdout metrics fail thresholds (unless explicitly skipped for emergency).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("econith.training.deploy_gate")

# Conservative defaults — override via CLI flags on deploy.py.
DEFAULT_MIN_SHARPE = 0.25
DEFAULT_MAX_DRAWDOWN = 0.30
DEFAULT_MIN_ROWS = 500


def load_metrics_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"metrics report must be a JSON object: {path}")
    return data


def _primary_forward_col(df) -> str:
    """Prefer scalp 1m; else first available forward_return_* (swing presets)."""
    preferred = (
        "forward_return_1m",
        "forward_return_5m",
        "forward_return_15m",
        "forward_return_1h",
        "forward_return_4h",
        "forward_return_1d",
    )
    for col in preferred:
        if col in df.columns:
            return col
    for col in df.columns:
        if str(col).startswith("forward_return_"):
            return str(col)
    raise ValueError(
        "holdout must contain a forward_return_* column "
        "(scalp: forward_return_1m; swing: forward_return_1h / _1d)"
    )


def run_holdout_backtest(holdout_parquet: Path) -> dict[str, Any]:
    """Backtest only precomputed, timestamped out-of-sample predictions.

    The holdout *label* is used to score outcomes, NEVER to create positions.
    Refuse raw labeled holdouts: they contain no evidence of predictive skill.
    Prediction provenance is an operator assertion; an independent data/model
    audit is still required before interpreting any performance as real.
    """
    import numpy as np
    import pandas as pd

    from training.evaluation.backtest import BacktestConfig, BacktestEngine

    if not holdout_parquet.exists():
        raise FileNotFoundError(f"holdout parquet missing: {holdout_parquet}")

    df = pd.read_parquet(holdout_parquet)
    fwd_col = _primary_forward_col(df)
    horizon = fwd_col.removeprefix("forward_return_")
    prediction_col = f"oos_prediction_{horizon}"
    required = {prediction_col, "ts_ms", "prediction_asof_ms", "prediction_model_id"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(
            "refusing look-ahead backtest: missing genuine out-of-sample "
            f"prediction fields {missing}. Train on earlier data, write a "
            f"timestamped {prediction_col} from the frozen model, then evaluate."
        )
    if df.empty:
        raise ValueError("holdout is empty")

    for symbol, group in df.groupby("symbol" if "symbol" in df.columns else
                                     pd.Series("UNKNOWN", index=df.index)):
        timestamps = pd.to_numeric(group["ts_ms"], errors="coerce")
        asof = pd.to_numeric(group["prediction_asof_ms"], errors="coerce")
        if (timestamps.isna().any() or asof.isna().any()
                or (timestamps < 0).any() or (asof < 0).any()
                or (asof > timestamps).any()):
            raise ValueError(f"{symbol}: prediction timestamp is missing or in the future")
        if not timestamps.is_monotonic_increasing or timestamps.duplicated().any():
            raise ValueError(f"{symbol}: holdout timestamps must be increasing and unique")

    actual = pd.to_numeric(df[fwd_col], errors="coerce").to_numpy(dtype="float64")
    preds = pd.to_numeric(df[prediction_col], errors="coerce").to_numpy(dtype="float64")
    if not np.isfinite(actual).all() or not np.isfinite(preds).all():
        raise ValueError("nonfinite label or prediction in holdout")
    model_ids = df["prediction_model_id"].astype("string")
    if model_ids.isna().any() or model_ids.str.strip().eq("").any():
        raise ValueError("prediction_model_id must identify a frozen out-of-sample model")
    # Reject two common variants of the old oracle: copying future returns or
    # their signs into the prediction column. This is not a complete audit.
    if np.array_equal(preds, actual) or np.array_equal(preds, np.sign(actual)):
        raise ValueError("prediction column reproduces realized future labels")

    def signal_fn(frame):
        forecast = frame[prediction_col].to_numpy(dtype="float64")
        return np.sign(forecast)

    result = BacktestEngine(BacktestConfig(return_column=fwd_col)).run_frame(
        df, signal_fn
    )
    metrics = result.metrics.to_dict()
    metrics.update(
        rows=int(result.rows),
        signal_col=prediction_col,
        realized_col=fwd_col,
        evaluation_protocol="chronological_holdout_oos",
        prediction_model_id=sorted(model_ids.unique().tolist()),
    )
    return metrics

def evaluate_gate(
    metrics: dict[str, Any],
    *,
    min_sharpe: float = DEFAULT_MIN_SHARPE,
    max_drawdown: float = DEFAULT_MAX_DRAWDOWN,
    min_rows: int = DEFAULT_MIN_ROWS,
) -> tuple[bool, str]:
    if metrics.get("evaluation_protocol") != "chronological_holdout_oos":
        return False, "missing chronological out-of-sample evaluation protocol"
    if not str(metrics.get("signal_col", "")).startswith("oos_prediction_"):
        return False, "missing out-of-sample prediction signal"
    if not metrics.get("prediction_model_id"):
        return False, "missing frozen model provenance"
    rows = int(metrics.get("rows") or 0)
    sharpe = float(metrics.get("annualized_sharpe") or 0.0)
    dd = abs(float(metrics.get("max_drawdown") or 0.0))
    if rows < min_rows:
        return False, f"rows={rows} < min_rows={min_rows}"
    if sharpe < min_sharpe:
        return False, f"annualized_sharpe={sharpe:.4f} < min_sharpe={min_sharpe}"
    if dd > max_drawdown:
        return False, f"max_drawdown={dd:.4f} > max_drawdown={max_drawdown}"
    return True, "ok"


def gate_or_raise(
    *,
    holdout: Path | None,
    metrics_report: Path | None,
    min_sharpe: float,
    max_drawdown: float,
    min_rows: int,
    skip: bool,
) -> dict[str, Any]:
    if skip:
        raise SystemExit(
            "deployment refused: --skip-backtest is disabled for model activation"
        )

    if metrics_report is not None:
        metrics = load_metrics_report(metrics_report)
    elif holdout is not None:
        metrics = run_holdout_backtest(holdout)
    else:
        raise SystemExit(
            "deployment refused — provide --holdout or --backtest-report "
            "(or --skip-backtest for emergency only)"
        )

    ok, reason = evaluate_gate(
        metrics,
        min_sharpe=min_sharpe,
        max_drawdown=max_drawdown,
        min_rows=min_rows,
    )
    report = {"skipped": False, "passed": ok, "reason": reason, "metrics": metrics}
    if not ok:
        raise SystemExit(f"deployment refused — backtest gate failed: {reason}")
    logger.info("backtest gate PASSED (%s)", reason)
    return report
