"""ECONITH :: training.evaluation

Offline validation engine: backtest harness + vectorised performance metrics.
"""
from __future__ import annotations

from training.evaluation.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    PerformanceMetrics,
    compute_metrics,
)

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "PerformanceMetrics",
    "compute_metrics",
]
