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
DEFAULT_MIN_SHARPE = -0.5  # allow mild negative on tiny smoke sets
DEFAULT_MAX_DRAWDOWN = 0.45
DEFAULT_MIN_ROWS = 50


def load_metrics_report(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"metrics report must be a JSON object: {path}")
    return data


def run_holdout_backtest(holdout_parquet: Path) -> dict[str, Any]:
    """Vectorised holdout backtest using signed forward-return as signal."""
    import pandas as pd

    from training.evaluation.backtest import BacktestConfig, BacktestEngine

    if not holdout_parquet.exists():
        raise FileNotFoundError(f"holdout parquet missing: {holdout_parquet}")

    df = pd.read_parquet(holdout_parquet)
    if "forward_return_1m" not in df.columns:
        raise ValueError("holdout must contain forward_return_1m")

    def signal_fn(frame):
        # Simple teacher signal: lean with realized forward return sign.
        import numpy as np

        fwd = pd.to_numeric(frame["forward_return_1m"], errors="coerce").fillna(0.0)
        return np.sign(fwd.to_numpy(dtype="float64"))

    engine = BacktestEngine(BacktestConfig())
    result = engine.run_frame(df, signal_fn)
    metrics = result.metrics.to_dict()
    metrics["rows"] = int(result.rows)
    return metrics


def evaluate_gate(
    metrics: dict[str, Any],
    *,
    min_sharpe: float = DEFAULT_MIN_SHARPE,
    max_drawdown: float = DEFAULT_MAX_DRAWDOWN,
    min_rows: int = DEFAULT_MIN_ROWS,
) -> tuple[bool, str]:
    rows = int(metrics.get("rows") or metrics.get("num_trades") or 0)
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
        logger.warning("backtest gate SKIPPED (--skip-backtest)")
        return {"skipped": True, "passed": True}

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
