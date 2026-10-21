"""Preregistered 12-market fixed-strategy challenge — never optimize on results.

One finite, honest historical research batch. Not forward paper, demo,
autonomous execution, predicted PnL or permission to deposit. Each failure
and loss appears in the evidence report. No real trading interfaces.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from econith.quant.paper_v1 import SymbolRules, validated_candles
from econith.quant.public_binance_data import write_snapshot
from scripts.paper_trade_quickstart import _capture_exact_public_pair, _simulate

FROZEN = Path(__file__).resolve().parents[1] / "docs" / "research" / "P2_02_FROZEN_PAPER_MATRIX_20261021.json"
PASS = "EVALUATION_COMPLETED_ALL_OUTCOMES_UNFILTERED_NOT_PROFIT_PROOF"
INCOMPLETE = "EVALUATION_INCOMPLETE_DATA_MUST_NOT_SELECT_WINNERS"
CAPITAL = "REAL_CAPITAL_NOT_AUTHORIZED"
PERIOD = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _write(path: Path, obj: dict) -> None:
    body = (json.dumps(obj, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as file:
        file.write(body)
        file.flush()
        os.fsync(file.fileno())


def read_plan(path: Path = FROZEN) -> dict:
    p = json.loads(path.read_text(encoding="utf-8"))
    if (p.get("schema") != "econith-p2-matrix-frozen-v1"
        or p.get("reference_commit") != "81c06edb50e2f311fd417c641f0927f554223901"
        or p.get("symbols") != ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
        or p.get("intervals") != ["1h", "4h", "1d"]
        or p.get("requested_candles_per_case") != 500
        or p.get("virtual_capital_usdt") != 100
        or p.get("fixed_strategy") != {
            "lookback_closed_bars": 24, "entry_threshold": 0.01,
            "max_asset_weight": 0.2, "max_total_weight": 0.2,
            "max_drawdown": 0.15, "fill_convention": "next_historical_open",
            "strategy_previously_inspected": True, "retune_after_results": False,
        } or p.get("cost_model") != [
            {"name": "base", "fee_bps": 10, "slippage_bps": 2, "half_spread_bps": 1},
            {"name": "stress3x", "fee_bps": 30, "slippage_bps": 6, "half_spread_bps": 3},
        ]):
        raise ValueError("unapproved change to pre-registered paper experiment")
    return p


def _benchmark_20pct(snapshot: dict, *, stress: int) -> dict:
    symbol = next(iter(snapshot["market"]))
    bars = validated_candles(
        snapshot["market"][symbol], timeframe_ms=PERIOD[snapshot["timeframe"]],
        asof_ms=snapshot["asof_ms"],
    )
    rules = SymbolRules(**snapshot["rules"][symbol])
    if len(bars) <= 25:
        raise ValueError("too few bars for frozen benchmark")
    initial = 100.
    buy_open = bars[25].open  # first time fixed strategy may enter
    fee = 10 * stress / 10000.
    friction = (2 + 1) * stress / 10000.
    buy_fill = buy_open * (1 + friction)
    qty = rules.floor_qty((initial * .20) / (buy_fill * (1 + fee)))
    if qty * buy_fill < rules.min_notional:
        qty = 0
    cash = initial - qty * buy_fill * (1 + fee)
    equity = cash + qty * bars[-1].close
    if not (math.isfinite(equity) and cash >= -1e-8):
        raise ValueError("buy-and-hold benchmark invalid")
    return {
        "policy": "20pct initial capital one hypothetical buy then hold, same model fees",
        "quantity": qty, "ending_equity_usdt": round(equity, 8),
        "net_pnl_usdt": round(equity - initial, 8),
        "net_return_pct": round((equity / initial - 1) * 100, 6),
    }


def evaluate_market(snapshot: dict) -> dict:
    if (snapshot.get("timeframe") not in PERIOD
        or len(snapshot.get("market", {})) != 1
        or set(snapshot.get("market", {})) != set(snapshot.get("rules", {}))):
        raise ValueError("unexpected market interval or symbol/sizing scope")
    symbol = next(iter(snapshot["market"]))
    rows = snapshot["market"][symbol]
    if not 300 <= len(rows) <= 500:
        raise ValueError("must have >=300 contiguous actually closed bars")
    _ = validated_candles(
        rows, timeframe_ms=PERIOD[snapshot["timeframe"]],
        asof_ms=snapshot["asof_ms"],
    )
    cut = int(len(rows) * 0.70)
    if cut < 200 or len(rows) - cut < 90:
        raise ValueError("too few bars for chronological late-window evaluation")
    # The 25 preceding closed bars are ONLY feature warmup, not invested
    # history. The first simulated order is at the first 30%-tail bar OPEN.
    late = dict(snapshot, market={symbol: rows[cut-25:]})
    result = {"symbol": symbol, "timeframe": snapshot["timeframe"],
              "closed_bars": len(rows), "late_eval_start_open_ms": int(rows[cut][0]),
              "late_bars_evaluated": len(rows) - cut,
              "late_warmup_bars": 25, "selection_or_retuning": False,
              "source_asof_ms": snapshot["asof_ms"], "windows": {}}
    for name, scope in (("full", snapshot), ("late30", late)):
        base = _simulate(scope, capital=100., stress=1)
        stress = _simulate(scope, capital=100., stress=3)
        hold_base = _benchmark_20pct(scope, stress=1)
        hold_stress = _benchmark_20pct(scope, stress=3)
        a = base["summary"]
        s = stress["summary"]
        result["windows"][name] = {
            "base": a, "stress3x": s,
            "benchmark20_base": hold_base,
            "benchmark20_stress3x": hold_stress,
            "beats_cash_base": a["net_pnl_usdt"] > 0,
            "beats_cash_stress3x": s["net_pnl_usdt"] > 0,
            "beats_buyhold20_stress3x": (
                s["net_pnl_usdt"] > hold_stress["net_pnl_usdt"]),
            "trade_count_nonzero": a["simulated_fill_count"] > 0,
            "cash_and_position_ledger_audited": (
                base["audit"]["status"] == "SIMULATED_LEDGER_RECONCILED"
                and stress["audit"]["status"] == "SIMULATED_LEDGER_RECONCILED"
            ),
        }
    return result


def run_matrix(root: Path, *, capture=_capture_exact_public_pair,
               plan_file: Path = FROZEN) -> dict:
    p = read_plan(plan_file)
    if (not root.is_absolute() or root != root.resolve()
        or root.exists() or root.is_symlink() or not root.parent.is_dir()
        or root.parent.is_symlink()):
        raise ValueError("new unaliased absolute private evidence root required")
    root.mkdir(mode=0o700)
    _write(root / "preregistered_policy.json", p)
    results = []
    for symbol in p["symbols"]:
        for interval in p["intervals"]:
            name = symbol.lower() + "_" + interval
            outcome = {"symbol": symbol, "interval": interval,
                       "status": "CAPTURE_FAILED_NO_INFERENCE"}
            try:
                # Data collection only; no exchange accounts or order APIs.
                snapshot = capture([symbol], limit=500, interval=interval)
                if (snapshot.get("source") != "binance_spot_public"
                    or snapshot.get("timeframe") != interval
                    or snapshot.get("symbols") != [symbol]
                    or set(snapshot.get("market", {})) != {symbol}
                    or set(snapshot.get("rules", {})) != {symbol}):
                    raise ValueError("exact publicly listed spot pair not verified")
                evaluation = evaluate_market(snapshot)
                dataset_root = root / name
                manifest = write_snapshot(snapshot, dataset_root)
                _write(dataset_root / "case.json", evaluation)
                outcome = {
                    "symbol": symbol, "interval": interval,
                    "status": "CAPTURE_AND_SIMULATION_RECONCILED",
                    "source_snapshot_sha256": manifest["sha256"],
                    "result": evaluation,
                }
            except Exception as exc:
                # NEVER omit losses/errors from the 12-case denominator.
                outcome["status"] = "QUARANTINED_NO_EVIDENCE_OF_EDGE"
                outcome["error_type"] = type(exc).__name__
            results.append(outcome)
            print(json.dumps({
                "case": name, "status": outcome["status"],
                "late_base_pct": outcome.get("result", {}).get("windows", {})
                    .get("late30", {}).get("base", {}).get("return_pct"),
                "late_stress3x_pct": outcome.get("result", {}).get("windows", {})
                    .get("late30", {}).get("stress3x", {}).get("return_pct"),
            }, sort_keys=True), flush=True)

    good = [x for x in results if x["status"] ==
            "CAPTURE_AND_SIMULATION_RECONCILED"]
    late = [x["result"]["windows"]["late30"] for x in good]
    agg = {
        "schema": "econith-p2-matrix-12-unfiltered-v1",
        "verdict": PASS if len(good) == len(results) == 12 else INCOMPLETE,
        "expected_cases": 12, "successful_data_cases": len(good),
        "failed_data_cases": len(results) - len(good),
        "late30_positive_base_cases": sum(
            x["beats_cash_base"] for x in late),
        "late30_positive_after_3x_cost_cases": sum(
            x["beats_cash_stress3x"] for x in late),
        "late30_beats_cost_matched_20pct_buyhold_cases": sum(
            x["beats_buyhold20_stress3x"] for x in late),
        "late30_positive_3x_and_beats_buyhold_and_trades_cases": sum(
            x["beats_cash_stress3x"] and
            x["beats_buyhold20_stress3x"] and x["trade_count_nonzero"]
            for x in late),
        "model_parameter_retuning_performed": False,
        "data_was_previously_inspected_by_development": True,
        "statistically_independent_cases": False,
        "prospective_out_of_sample_validated": False,
        "real_exchange_orders": 0,
        "real_money_authorized": False,
        "capital_readiness": CAPITAL,
        "cases": results,
    }
    _write(root / "unfiltered_matrix.json", agg)
    return agg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-public-market-data", action="store_true")
    parser.add_argument("--simulation-only", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if not (args.allow_public_market_data and args.simulation_only):
        parser.error("explicit read-only public data and simulation opt-in required")
    try:
        result = run_matrix(args.output_dir)
    except (ValueError, OSError, TypeError, RuntimeError) as exc:
        print(json.dumps({"verdict": INCOMPLETE,
                          "failure_class": type(exc).__name__,
                          "real_exchange_orders": 0,
                          "capital_readiness": CAPITAL}), flush=True)
        return 2
    summary = {k: v for k, v in result.items() if k != "cases"}
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if result["verdict"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
