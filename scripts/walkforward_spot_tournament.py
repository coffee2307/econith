"""Chronological strategy tournament with a locked evaluation window.

This is RESEARCH ONLY: a historical 70/30 holdout split is not sufficient for
live readiness. Candidate selection cannot use any later bars. Cash is the
control and wins if no candidate survives conservative training friction.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from econith.quant.paper_audit import audit_paper_report
from econith.quant.paper_v1 import PaperConfig, SymbolRules, replay_spot, validated_candles
from econith.quant.public_binance_data import load_snapshot

_PERIODS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
# Predeclared hypotheses. Never retune these based on the locked 30% outcome.
_CANDIDATES = (
    ("momentum_12_30bps", 12, .003),
    ("momentum_24_100bps", 24, .01),
    ("momentum_48_200bps", 48, .02),
)


def _make_cfg(capital: float, ms: int, lookback: int, threshold: float,
              stress: int) -> PaperConfig:
    return PaperConfig(
        initial_cash=capital, timeframe_ms=ms, lookback=lookback,
        entry_threshold=threshold, fee_bps=10 * stress,
        slippage_bps=2 * stress, half_spread_bps=1 * stress,
    )


def _bootstrap_lower90(equity: list[dict], *, seed: int = 2708, nboot: int = 400) -> float:
    """10th percentile of *block-resampled* compounded portfolio returns.

    Returns a descriptive one-sided 90% bootstrap bound under stationarity
    assumptions. NOT a 90% probability of future profit.
    """
    values = [float(p["equity"]) for p in equity]
    if len(values) < 90 or any(v <= 0 or not math.isfinite(v) for v in values):
        raise ValueError("insufficient clean portfolio equity series")
    logrets = [math.log(b / a) for a, b in zip(values, values[1:])]
    count = len(logrets)
    block = max(2, round(math.sqrt(count)))
    rng = random.Random(seed)
    outcomes = []
    for _ in range(nboot):
        synthetic = []
        while len(synthetic) < count:
            start = rng.randrange(count)
            synthetic.extend(logrets[(start + k) % count] for k in range(block))
        result = math.expm1(sum(synthetic[:count]))
        outcomes.append(result)
    outcomes.sort()
    return round(outcomes[int((nboot - 1) * .10)] * 100, 5)


def tournament(snapshot: dict, *, capital: float = 1000.0) -> dict:
    interval = str(snapshot["timeframe"])
    if interval not in _PERIODS:
        raise ValueError("unsupported interval")
    if not math.isfinite(capital) or capital <= 0:
        raise ValueError("capital must be positive")
    ms = _PERIODS[interval]
    raw = snapshot["market"]
    rules = {s: SymbolRules(**d) for s, d in snapshot["rules"].items()}
    market = {s: validated_candles(c, timeframe_ms=ms, asof_ms=int(snapshot["asof_ms"]))
              for s, c in raw.items()}
    if not market or market.keys() != rules.keys():
        raise ValueError("market/rules symbol mismatch")
    first = next(iter(market.values()))
    total = len(first)
    cut = int(total * .70)
    if cut < 200 or total - cut < 90:
        raise ValueError("need at least 200 training bars and 90 locked test bars")
    if any(len(c) != total or [b.open_ms for b in c] != [b.open_ms for b in first]
           for c in market.values()):
        raise ValueError("misaligned asset timelines")

    dev = {s: series[:cut] for s, series in market.items()}
    locked = {s: series[cut:] for s, series in market.items()}
    trials = []
    best_name, best_score = "CASH", 0.0
    best_parameters = None
    for name, lookback, threshold in _CANDIDATES:
        train_base = replay_spot(dev, rules, _make_cfg(capital, ms, lookback, threshold, 1))
        train_stress = replay_spot(dev, rules, _make_cfg(capital, ms, lookback, threshold, 3))
        # Train-only score. Correct for drawdown and fee fragility.
        score = min(train_base.net_return, train_stress.net_return) - .5 * train_base.max_drawdown
        trials.append({
            "candidate": name, "lookback": lookback, "threshold": threshold,
            "train_return_pct": round(train_base.net_return * 100, 5),
            "train_stress_return_pct": round(train_stress.net_return * 100, 5),
            "train_max_drawdown_pct": round(train_base.max_drawdown * 100, 5),
            "train_fills": len(train_base.fills),
            "training_score_pct": round(score * 100, 5),
        })
        if score > best_score:
            best_score, best_name = score, name
            best_parameters = (lookback, threshold)

    result: dict = {
        "method": "one predeclared historical chronological 70/30 holdout",
        "selection_uses_locked_test_bars": False,
        "universe": sorted(market),
        "timeframe": interval,
        "capital_simulated": capital,
        "train_bars": cut,
        "locked_test_bars": total - cut,
        "locked_test_first_open_ms": first[cut].open_ms,
        "candidate_count": len(_CANDIDATES),
        "candidate_training_trials": trials,
        "selected_on_training_only": best_name,
        "confidence_90_pct_of_future_profit": None,
        "execution_mode": "SIMULATION",
        "ready_for_exchange_demo": False,
        "ready_for_real_money": False,
    }
    if best_parameters is None:
        result["decision"] = "NO_TRADE_ALL_CANDIDATES_FAILED_TRAINING_COST_RISK"
        result["locked_test"] = {"tested": False, "reason": "cash control won"}
        return result

    lookback, threshold = best_parameters
    base_run = replay_spot(locked, rules, _make_cfg(capital, ms, lookback, threshold, 1))
    stress_run = replay_spot(locked, rules, _make_cfg(capital, ms, lookback, threshold, 3))
    base_ledger = audit_paper_report(base_run.to_dict())
    stressed_ledger = audit_paper_report(stress_run.to_dict())
    lower = _bootstrap_lower90(base_run.equity_curve)
    exploratory_pass = (
        base_run.net_return > 0
        and stress_run.net_return > 0
        and lower > 0
        and base_run.max_drawdown < .15
        and not base_run.halted
        and len(base_run.fills) >= 25
    )
    result["decision"] = "CANDIDATE_ONLY" if exploratory_pass else "REJECTED_IN_LOCKED_TEST"
    result["locked_test"] = {
        "tested": True,
        "base_net_return_pct": round(base_run.net_return * 100, 5),
        "stress_3x_net_return_pct": round(stress_run.net_return * 100, 5),
        "base_max_drawdown_pct": round(base_run.max_drawdown * 100, 5),
        "base_order_legs": len(base_run.fills),
        "base_fees": base_ledger["explicit_fees_paid"],
        "stress_fees": stressed_ledger["explicit_fees_paid"],
        "ledger_reconciled": (
            base_ledger["status"] == "SIMULATED_LEDGER_RECONCILED"
            and stressed_ledger["status"] == "SIMULATED_LEDGER_RECONCILED"
        ),
        "historical_block_bootstrap_90pct_lower_return_pct": lower,
        "exploratory_pass": exploratory_pass,
        "bootstrap_note": "Descriptive under resampled historical stationarity assumptions, NOT 90% confidence in future trading profits",
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline frozen 70/30 spot strategy contest")
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--capital", type=float, default=1000)
    args = parser.parse_args(argv)
    if args.output.resolve() in {
        (args.snapshot_dir / "market.json").resolve(),
        (args.snapshot_dir / "rules.json").resolve(),
        (args.snapshot_dir / "manifest.json").resolve(),
    }:
        parser.error("output must not overwrite snapshot inputs")
    report = tournament(load_snapshot(args.snapshot_dir), capital=args.capital)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(report["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
