"""ECONITH :: training.evaluation.backtest

Offline validation engine — a vectorised backtest harness + performance metrics.

The engine streams labeled/raw feature Parquet, applies a signal function to
produce a target position per row (per symbol, isolated), then simulates
execution with explicit **fee (bps)**, **slippage (bps)** and **bid-ask spread**
friction. It produces a net-of-cost equity curve and a full institutional
metric report.

Mathematical rigor: every metric is guarded against NaN/Inf, empty series and
zero-division so a degenerate input never raises — it returns a well-defined
neutral value (0.0) instead.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger("econith.training.evaluation.backtest")

__all__ = [
    "BacktestConfig",
    "PerformanceMetrics",
    "BacktestResult",
    "BacktestEngine",
    "compute_metrics",
]

# Trading periods per year for annualisation (crypto trades 24/7/365).
_PERIODS_PER_YEAR = 365 * 24 * 60  # per-minute bar convention


@dataclass(slots=True)
class BacktestConfig:
    """Execution-friction and annualisation knobs."""

    fee_bps: float = 4.0                 # taker fee per side, basis points
    slippage_bps: float = 1.0            # market-impact slippage, basis points
    spread_bps: float = 2.0              # half-spread friction crossing the book
    periods_per_year: int = _PERIODS_PER_YEAR
    return_column: str = "forward_return_1m"
    regime_column: str = "regime"
    symbol_column: str = "symbol"


@dataclass(slots=True)
class PerformanceMetrics:
    """A full institutional performance report."""

    total_return: float = 0.0
    annualized_return: float = 0.0
    annualized_sharpe: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_start: Optional[str] = None
    max_drawdown_trough: Optional[str] = None
    profit_factor: float = 0.0
    win_rate: float = 0.0
    turnover: float = 0.0
    num_trades: int = 0
    pnl_by_regime: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_return": round(self.total_return, 6),
            "annualized_return": round(self.annualized_return, 6),
            "annualized_sharpe": round(self.annualized_sharpe, 4),
            "sortino_ratio": round(self.sortino_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_start": self.max_drawdown_start,
            "max_drawdown_trough": self.max_drawdown_trough,
            "profit_factor": round(self.profit_factor, 4),
            "win_rate": round(self.win_rate, 4),
            "turnover": round(self.turnover, 4),
            "num_trades": self.num_trades,
            "pnl_by_regime": {k: round(v, 6) for k, v in self.pnl_by_regime.items()},
        }


@dataclass(slots=True)
class BacktestResult:
    """The simulated net-return stream + its computed metrics."""

    net_returns: Any                     # np.ndarray of per-step net returns
    equity_curve: Any                    # np.ndarray of cumulative equity (start=1.0)
    metrics: PerformanceMetrics
    rows: int


# ---------------------------------------------------------------------------
# Metric primitives (NaN/Inf/zero-division safe)
# ---------------------------------------------------------------------------
def _safe(arr: Any, np: Any) -> Any:
    """Coerce to finite float64, replacing NaN/Inf with 0.0."""
    a = np.asarray(arr, dtype="float64")
    return np.nan_to_num(a, nan=0.0, posinf=0.0, neginf=0.0)


def compute_metrics(
    net_returns: Any,
    *,
    periods_per_year: int = _PERIODS_PER_YEAR,
    timestamps: Any = None,
    regimes: Any = None,
    gross_pnl: Any = None,
) -> PerformanceMetrics:
    """Vectorised performance metrics from a per-step net-return stream."""
    import numpy as np

    r = _safe(net_returns, np)
    n = int(r.size)
    if n == 0:
        return PerformanceMetrics()

    # Equity curve from compounded net returns.
    equity = np.cumprod(1.0 + r)
    total_return = float(equity[-1] - 1.0)

    # Annualisation.
    mean = float(np.mean(r))
    std = float(np.std(r, ddof=1)) if n > 1 else 0.0
    ann_return = mean * periods_per_year
    ann_sharpe = (mean / std * math.sqrt(periods_per_year)) if std > 0 else 0.0

    # Sortino: downside deviation only.
    downside = r[r < 0.0]
    dd_std = float(np.sqrt(np.mean(downside ** 2))) if downside.size else 0.0
    sortino = (mean / dd_std * math.sqrt(periods_per_year)) if dd_std > 0 else 0.0

    # Max drawdown with precise peak/trough timestamping.
    running_peak = np.maximum.accumulate(equity)
    drawdowns = np.where(running_peak > 0, 1.0 - equity / running_peak, 0.0)
    trough_idx = int(np.argmax(drawdowns))
    max_dd = float(drawdowns[trough_idx])
    peak_idx = int(np.argmax(equity[: trough_idx + 1])) if trough_idx >= 0 else 0

    def _stamp(idx: int) -> Optional[str]:
        if timestamps is None:
            return None
        try:
            ts = np.asarray(timestamps)
            val = ts[idx]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                return datetime.fromtimestamp(float(val) / 1000.0, tz=timezone.utc).isoformat()
            return str(val)
        except Exception:  # noqa: BLE001 - timestamps are best-effort context
            return None

    # Profit factor + win rate.
    gains = float(np.sum(r[r > 0.0]))
    losses = float(-np.sum(r[r < 0.0]))
    profit_factor = (gains / losses) if losses > 0 else (gains if gains > 0 else 0.0)
    wins = int(np.sum(r > 0.0))
    win_rate = (wins / n) if n else 0.0

    # PnL by regime (best-effort; requires a parallel regime label array).
    pnl_by_regime: dict[str, float] = {}
    if regimes is not None:
        reg = np.asarray(regimes)
        if reg.shape[0] == n:
            for label in np.unique(reg):
                mask = reg == label
                pnl_by_regime[str(label)] = float(np.sum(r[mask]))

    return PerformanceMetrics(
        total_return=total_return,
        annualized_return=ann_return,
        annualized_sharpe=ann_sharpe,
        sortino_ratio=sortino,
        max_drawdown=max_dd,
        max_drawdown_start=_stamp(peak_idx),
        max_drawdown_trough=_stamp(trough_idx),
        profit_factor=profit_factor,
        win_rate=win_rate,
        num_trades=wins + int(np.sum(r < 0.0)),
        pnl_by_regime=pnl_by_regime,
    )


# ---------------------------------------------------------------------------
# The backtest engine
# ---------------------------------------------------------------------------
# A signal function maps a per-symbol feature frame -> target position array in
# [-1, 1] (short..long), one entry per row.
SignalFn = Callable[[Any], Any]


class BacktestEngine:
    """Streams feature Parquet and simulates net-of-cost execution per symbol."""

    def __init__(self, config: Optional[BacktestConfig] = None) -> None:
        self._cfg = config or BacktestConfig()

    def _friction_per_turn(self) -> float:
        """Total round-trip-normalised friction as a fraction (per unit turnover)."""
        return (self._cfg.fee_bps + self._cfg.slippage_bps + self._cfg.spread_bps) / 10_000.0

    def run_frame(self, frame: Any, signal_fn: SignalFn) -> BacktestResult:
        """Backtest a single already-loaded DataFrame across its symbols."""
        import numpy as np
        import pandas as pd  # noqa: F401 - ensures pandas present for callers

        cfg = self._cfg
        if cfg.symbol_column not in frame.columns:
            frame = frame.copy()
            frame[cfg.symbol_column] = "UNKNOWN"

        all_net: list[Any] = []
        all_ts: list[Any] = []
        all_reg: list[Any] = []
        total_turnover = 0.0
        friction = self._friction_per_turn()

        for _symbol, group in frame.groupby(cfg.symbol_column, sort=True):
            g = group.sort_values("ts_ms") if "ts_ms" in group.columns else group
            if cfg.return_column not in g.columns:
                logger.warning("frame lacks %s; skipping symbol", cfg.return_column)
                continue
            fwd = _safe(g[cfg.return_column].to_numpy(), np)
            pos = _safe(signal_fn(g), np)
            if pos.shape[0] != fwd.shape[0]:
                logger.warning("signal length mismatch; skipping symbol")
                continue
            pos = np.clip(pos, -1.0, 1.0)

            # Gross per-step return = position * realised forward return.
            gross = pos * fwd
            # Turnover = |Δposition| each step; friction charged on turnover.
            turnover = np.abs(np.diff(pos, prepend=0.0))
            total_turnover += float(np.sum(turnover))
            net = gross - turnover * friction

            all_net.append(net)
            if "ts_ms" in g.columns:
                all_ts.append(g["ts_ms"].to_numpy())
            if cfg.regime_column in g.columns:
                all_reg.append(g[cfg.regime_column].to_numpy())

        if not all_net:
            return BacktestResult(
                net_returns=np.array([]), equity_curve=np.array([]),
                metrics=PerformanceMetrics(), rows=0,
            )

        net_returns = np.concatenate(all_net)
        timestamps = np.concatenate(all_ts) if all_ts else None
        regimes = np.concatenate(all_reg) if all_reg and len(all_reg) == len(all_net) else None

        metrics = compute_metrics(
            net_returns,
            periods_per_year=cfg.periods_per_year,
            timestamps=timestamps,
            regimes=regimes,
        )
        metrics.turnover = total_turnover
        equity = np.cumprod(1.0 + _safe(net_returns, np))
        return BacktestResult(
            net_returns=net_returns, equity_curve=equity, metrics=metrics,
            rows=int(net_returns.size),
        )

    def run_parquet(self, path: str | Path, signal_fn: SignalFn) -> BacktestResult:
        """Load a labeled/raw feature Parquet (or a directory of shards) + backtest."""
        import pandas as pd

        p = Path(path)
        if p.is_dir():
            shards = sorted(p.rglob("*.parquet"))
            if not shards:
                raise SystemExit(f"no parquet shards under {p}")
            frame = pd.concat((pd.read_parquet(s) for s in shards), ignore_index=True)
        else:
            frame = pd.read_parquet(p)
        return self.run_frame(frame, signal_fn)


# ---------------------------------------------------------------------------
# Baseline signals + CLI (for ``make backtest-baseline``)
# ---------------------------------------------------------------------------
def momentum_signal(frame: Any) -> Any:
    """A look-ahead-free baseline: take the sign of the *previous* price move.

    Position at row ``i`` is decided from information available up to ``i-1``
    only (the prior bar's price change), so this never peeks at the realised
    forward return it is graded against. It is a plumbing/sanity baseline, not a
    real strategy.
    """
    import numpy as np

    if "price" in frame.columns:
        price = _safe(frame["price"].to_numpy(), np)
    else:
        price = _safe(frame.get("mid", frame.iloc[:, 0]).to_numpy(), np)
    if price.size == 0:
        return np.zeros(0, dtype="float64")
    step = np.sign(np.diff(price, prepend=price[0]))
    pos = np.empty_like(step)
    pos[0] = 0.0
    pos[1:] = step[:-1]        # shift by one bar -> no look-ahead
    return pos


def flat_signal(frame: Any) -> Any:
    """A zero-exposure control baseline (net return must be ~0)."""
    import numpy as np

    return np.zeros(len(frame), dtype="float64")


def long_signal(frame: Any) -> Any:
    """An always-long baseline that surfaces the raw net-of-cost asset drift."""
    import numpy as np

    return np.ones(len(frame), dtype="float64")


_BASELINES: dict[str, SignalFn] = {
    "momentum": momentum_signal,
    "flat": flat_signal,
    "long": long_signal,
}


def build_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(
        prog="backtest.py",
        description="ECONITH baseline backtest verification over a labeled feature set",
    )
    parser.add_argument(
        "--labeled",
        default="datasets/processed/quant_labeled.parquet",
        help="labeled parquet file or a directory of feature shards",
    )
    parser.add_argument(
        "--baseline",
        default="momentum",
        choices=sorted(_BASELINES.keys()),
        help="baseline signal to grade the plumbing with",
    )
    parser.add_argument("--fee-bps", type=float, default=4.0)
    parser.add_argument("--slippage-bps", type=float, default=1.0)
    parser.add_argument("--spread-bps", type=float, default=2.0)
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    import json as _json

    args = build_parser().parse_args(argv)
    config = BacktestConfig(
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
        spread_bps=args.spread_bps,
    )
    engine = BacktestEngine(config)
    result = engine.run_parquet(args.labeled, _BASELINES[args.baseline])
    report = {
        "input": str(args.labeled),
        "baseline": args.baseline,
        "rows": result.rows,
        "metrics": result.metrics.to_dict(),
    }
    print(_json.dumps(report, indent=2))
    logger.info(
        "backtest baseline '%s' complete: %d rows, sharpe=%.3f, max_dd=%.4f",
        args.baseline, result.rows,
        result.metrics.annualized_sharpe, result.metrics.max_drawdown,
    )


if __name__ == "__main__":
    main()
