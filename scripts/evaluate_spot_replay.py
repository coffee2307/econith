"""ECONITH offline, cost-aware evaluation for a *verified* public spot snapshot.

Purpose: expose fragile alpha and compare with honest cash/passive baselines.
This does not fit an ML model, prove out-of-sample alpha or trade on exchanges.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from econith.quant.paper_v1 import PaperConfig, SymbolRules, replay_spot, validated_candles
from econith.quant.public_binance_data import load_snapshot


def _metrics(equity: list[float], periods_per_year: int) -> dict:
    if len(equity) < 3 or any(not math.isfinite(x) or x <= 0 for x in equity):
        raise ValueError("invalid equity path")
    returns = [b / a - 1 for a, b in zip(equity, equity[1:])]
    avg = sum(returns) / len(returns)
    variance = sum((x - avg) ** 2 for x in returns) / (len(returns) - 1)
    vol = math.sqrt(variance)
    peak = equity[0]
    dd = 0.0
    for x in equity:
        peak = max(peak, x)
        dd = max(dd, 1 - x / peak)
    return {
        "net_return_pct": round(100 * (equity[-1] / equity[0] - 1), 4),
        "max_drawdown_pct": round(100 * dd, 4),
        "annualized_sharpe_no_rf": round(avg / vol * math.sqrt(periods_per_year), 4) if vol else 0.0,
        "bars": len(equity),
    }


def _passive_hold(bars: dict, rules: dict[str, SymbolRules],
                  cfg: PaperConfig, start_index: int) -> list[float]:
    """Spend <=20% of initial cash per asset once and hold (no rebalancing)."""
    symbols = sorted(bars)
    n = len(bars[symbols[0]])
    if start_index >= n:
        raise ValueError("not enough bars for passive baseline")
    fee = cfg.fee_bps / 10000
    adverse = (cfg.slippage_bps + cfg.half_spread_bps) / 10000
    cash = cfg.initial_cash
    quantities = {s: 0.0 for s in symbols}
    budget = cfg.initial_cash * min(cfg.max_asset_weight, cfg.max_total_weight / len(symbols))
    for s in symbols:
        px = bars[s][start_index].open * (1 + adverse)
        qty = rules[s].floor_qty(min(budget, cash / (1 + fee)) / px)
        total = qty * px * (1 + fee)
        if qty * px >= rules[s].min_notional and total <= cash:
            cash -= total
            quantities[s] = qty
    return [
        cfg.initial_cash if i < start_index else
        cash + sum(quantities[s] * bars[s][i].close for s in symbols)
        for i in range(n)
    ]


def evaluate(snapshot_dir: Path, *, config: PaperConfig | None = None) -> dict:
    snapshot = load_snapshot(snapshot_dir)
    ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[snapshot["timeframe"]]
    cfg = config or PaperConfig(timeframe_ms=ms)
    if cfg.timeframe_ms != ms:
        raise ValueError("config timeframe does not match snapshot")
    market = {
        s: validated_candles(raw, timeframe_ms=ms, asof_ms=snapshot["asof_ms"])
        for s, raw in snapshot["market"].items()
    }
    rules = {s: SymbolRules(**d) for s, d in snapshot["rules"].items()}
    common = [b.open_ms for b in market[next(iter(market))]]
    if len(common) < 50:
        raise ValueError("insufficient contiguous market observations")

    per_year = max(1, round(365 * 86_400_000 / ms))
    segments = [("full", 0, len(common))]
    # Exploratory robustness checks only: no tuning inside either window.
    cut = int(len(common) * .7)
    if cut >= cfg.lookback + 3 and len(common) - cut >= cfg.lookback + 3:
        segments += [("first_70pct", 0, cut), ("last_30pct", cut, len(common))]

    reports = {}
    for label, start, end in segments:
        sliced = {s: candles[start:end] for s, candles in market.items()}
        variants = {}
        for case, multiplier in (("friction_free", 0), ("base_cost", 1), ("triple_cost", 3)):
            trial = PaperConfig(
                initial_cash=cfg.initial_cash,
                lookback=cfg.lookback,
                entry_threshold=cfg.entry_threshold,
                max_asset_weight=cfg.max_asset_weight,
                max_total_weight=cfg.max_total_weight,
                fee_bps=cfg.fee_bps * multiplier,
                slippage_bps=cfg.slippage_bps * multiplier,
                half_spread_bps=cfg.half_spread_bps * multiplier,
                max_drawdown=cfg.max_drawdown,
                timeframe_ms=ms,
            )
            run = replay_spot(sliced, rules, trial)
            eq = [p["equity"] for p in run.equity_curve]
            m = _metrics(eq, per_year)
            m["fill_count"] = len(run.fills)
            m["halted"] = run.halted
            m["active_bars"] = sum(1 for p in run.equity_curve if p["exposure"] > 1e-7)
            m["final_equity"] = round(run.final_equity, 2)
            variants[case] = m
        # A buy-and-hold baseline is riskier than cash and has NO drawdown stop.
        benchmark = _passive_hold(
            sliced, rules, cfg, cfg.lookback + 1
        )
        variants["passive_buy_hold"] = _metrics(benchmark, per_year)
        variants["cash"] = _metrics([cfg.initial_cash] * (end - start), per_year)
        reports[label] = {
            "first_bar_ms": common[start], "last_bar_ms": common[end - 1],
            "rules": variants,
        }

    return {
        "dataset": {
            "asof_ms": snapshot["asof_ms"],
            "timeframe": snapshot["timeframe"],
            "symbols": sorted(market),
            "number_of_bars": len(common),
        },
        "methodology": {
            "strategy": "prior-closed-candle momentum; next-open synthetic fills",
            "lookback_bars": cfg.lookback,
            "entry_threshold": cfg.entry_threshold,
            "sample_split": "independently restarted 70/30 segments; exploratory NOT certified OOS",
            "fee_bps_one_side": cfg.fee_bps,
            "slippage_bps_one_side": cfg.slippage_bps,
            "half_spread_bps_one_side": cfg.half_spread_bps,
            "risk": "max-drawdown circuit breaker can overshoot between bars",
            "warning": "Sharpe is descriptive and unreliable on short or halted samples",
        },
        "scenarios": reports,
        "readiness": "NOT_READY_FOR_PROFITABILITY_OR_LIVE_EXECUTION",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline ECONITH spot strategy evaluation")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = evaluate(args.snapshot_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"{report['readiness']}: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
