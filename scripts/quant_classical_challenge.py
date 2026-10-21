"""Frozen classical quant candidate-vs-baseline research runner.

Every family/coin/timeframe × FULL/late30 × cost 1x/3x/5x is recorded.
Backtests use only prior CLOSED bars, NEXT historical open fills, the
same audited SPOT cash ledger and lot/notional rules. No orders/keys.
Markets already previously inspected: NOT new prospective OOS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from econith.quant.classical_challengers import NAMES, CANDIDATE_WARMUP, policy
from econith.quant.paper_audit import audit_paper_report
from econith.quant.paper_v1 import PaperConfig, SymbolRules, replay_spot, validated_candles
from econith.quant.public_binance_data import write_snapshot
from scripts.paper_trade_quickstart import _capture_exact_public_pair
from scripts.paper_unfiltered_matrix import PERIOD

FROZEN = (Path(__file__).resolve().parents[1] / "configs" / "research"
          / "classical_quant_frozen_20261021.json")
PASS = "ALL_60_STRATEGY_MARKET_CELLS_RECORDED_NOT_ALPHA_PROOF"
INCOMPLETE = "INCOMPLETE_CASES_NOT_EXCLUDED_FROM_DENOMINATOR"
REJECT = "REAL_CAPITAL_NOT_AUTHORIZED"


def _save(path: Path, value: dict) -> None:
    with path.open("xb") as f:
        f.write((json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode())
        f.flush()
        os.fsync(f.fileno())


def read_frozen_plan(path: Path = FROZEN) -> tuple[dict, str]:
    raw = path.read_bytes()
    obj = json.loads(raw)
    if (obj.get("schema") != "econith-classical-quant-challengers-20261021-v1"
        or obj.get("compare") != list(NAMES)
        or obj.get("universe") != ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]
        or obj.get("intervals") != ["1h", "4h", "1d"]
        or obj.get("requested_closed_candles") != 500
        or obj.get("virtual_capital_usdt") != 100
        or obj.get("lookback_warmup") != CANDIDATE_WARMUP
        or obj.get("max_asset_exposure") != .20
        or obj.get("max_total_exposure") != .20
        or obj.get("max_drawdown") != .15
        or obj.get("frictions") != [
            {"name": "base", "multiplier": 1, "fee_bps": 10,
             "slippage_bps": 2, "half_spread_bps": 1},
            {"name": "stress3x", "multiplier": 3, "fee_bps": 30,
             "slippage_bps": 6, "half_spread_bps": 3},
            {"name": "stress5x", "multiplier": 5, "fee_bps": 50,
             "slippage_bps": 10, "half_spread_bps": 5},
        ]):
        raise ValueError("frozen research matrix contract changed")
    return obj, hashlib.sha256(raw).hexdigest()


def _report_market(snapshot: dict, family: str, stress: dict) -> dict:
    symbol = snapshot["symbols"][0]
    bars = validated_candles(
        snapshot["market"][symbol], timeframe_ms=PERIOD[snapshot["timeframe"]],
        asof_ms=snapshot["asof_ms"],
    )
    rules = SymbolRules(**snapshot["rules"][symbol])
    cfg = PaperConfig(
        initial_cash=100., timeframe_ms=PERIOD[snapshot["timeframe"]],
        lookback=CANDIDATE_WARMUP,
        entry_threshold=.01,
        max_asset_weight=.2, max_total_weight=.2,
        max_drawdown=.15, fee_bps=stress["fee_bps"],
        slippage_bps=stress["slippage_bps"],
        half_spread_bps=stress["half_spread_bps"],
    )
    result = replay_spot({symbol: bars}, {symbol: rules}, cfg,
                         signal_policy=policy(family))
    audit = audit_paper_report(result.to_dict())
    if (audit["status"] != "SIMULATED_LEDGER_RECONCILED"
        or abs(audit["final_equity"] - result.final_equity) > 1e-5):
        raise ValueError("cash/position/fill PnL audit failed")
    # A passive risk-matched benchmark begins on exactly the SAME first
    # fill-eligible bar; there is no later intrabar knowledge.
    fill_ix = CANDIDATE_WARMUP + 1
    initial_px = bars[fill_ix].open
    fee = stress["fee_bps"] / 10000.
    slip = (stress["slippage_bps"] + stress["half_spread_bps"]) / 10000.
    buy_px = initial_px * (1 + slip)
    qty = rules.floor_qty(20. / (buy_px * (1 + fee)))
    if qty * buy_px < rules.min_notional:
        qty = 0.
    cash = 100. - qty * buy_px * (1 + fee)
    passive_eq = cash + qty * bars[-1].close
    if not (math.isfinite(passive_eq) and cash >= -1e-7
            and len(bars) > fill_ix + 1):
        raise ValueError("matched passive benchmark invalid")
    return {
        "capital_usdt": 100.,
        "net_pnl_usdt": round(result.final_equity - 100., 8),
        "net_return_pct": round(100 * result.net_return, 6),
        "max_drawdown_pct": round(100 * result.max_drawdown, 6),
        "simulated_fills": len(result.fills),
        "explicit_fees_usdt": audit["explicit_fees_paid"],
        "audited_realized_pnl_usdt": audit["realized_pnl"],
        "ending_cash_usdt": round(result.equity_curve[-1]["cash"], 8),
        "ending_exposure_usdt": round(result.equity_curve[-1]["exposure"], 8),
        "halted": result.halted,
        "benchmark_cash_pnl_usdt": 0.,
        "benchmark_20pct_buyhold_pnl_usdt": round(passive_eq - 100., 8),
        "beats_cash": result.final_equity > 100.,
        "beats_matched_buyhold": result.final_equity > passive_eq,
        "ledger_status": audit["status"],
        "orders_submitted": 0,
    }


def evaluate(snapshot: dict) -> dict:
    if (snapshot.get("source") != "binance_spot_public"
        or snapshot.get("timeframe") not in PERIOD
        or snapshot.get("symbols") is None or len(snapshot["symbols"]) != 1
        or set(snapshot["market"]) != set(snapshot["symbols"])
        or set(snapshot["rules"]) != set(snapshot["symbols"])):
        raise ValueError("not verified exact single-asset Binance public Spot research")
    sym = snapshot["symbols"][0]
    rows = snapshot["market"][sym]
    _ = validated_candles(rows, timeframe_ms=PERIOD[snapshot["timeframe"]],
                          asof_ms=snapshot["asof_ms"])
    if len(rows) < 400 or len(rows) > 500:
        raise ValueError("source history too short for preregistered candidate")
    cutoff = int(len(rows) * .70)
    history = rows[cutoff - (CANDIDATE_WARMUP + 1):]
    if len(history) < CANDIDATE_WARMUP + 1 + 90:
        raise ValueError("too few late30 bars")
    tail = dict(snapshot, market={sym: history})
    config, _ = read_frozen_plan()
    cases = {}
    for win, window in (("full", snapshot), ("late30", tail)):
        block = {}
        for family in NAMES:
            block[family] = {
                friction["name"]: _report_market(window, family, friction)
                for friction in config["frictions"]
            }
        cases[win] = block
    return {
        "symbol": sym, "timeframe": snapshot["timeframe"],
        "observed_closed_candles": len(rows),
        "window_latest30_evaluation_candles": len(rows) - cutoff,
        "signal_warmup_candles": CANDIDATE_WARMUP + 1,
        "latest30_start_open_ms": int(rows[cutoff][0]),
        "prior_closed_only": True, "all_candidates_same_first_fill_bar": True,
        "legacy_reference_comparison_not_pristine_oos": True,
        "windows": cases,
    }


def run_all(root: Path, *, capture=_capture_exact_public_pair) -> dict:
    config, config_hash = read_frozen_plan()
    if (not root.is_absolute() or root.exists() or root.is_symlink()
        or root != root.resolve() or not root.parent.is_dir()):
        raise ValueError("new private absolute report dir required")
    root.mkdir(mode=0o700)
    _save(root / "frozen_config.json", config)
    entries = []
    for symbol in config["universe"]:
        for interval in config["intervals"]:
            key = symbol.lower() + "_" + interval
            item = {"symbol": symbol, "interval": interval,
                    "status": "REFUSED_DATA_OR_SIMULATION"}
            try:
                snapshot = capture([symbol], limit=500, interval=interval)
                if (snapshot.get("symbols") != [symbol]
                    or snapshot.get("timeframe") != interval
                    or snapshot.get("source") != "binance_spot_public"):
                    raise ValueError("exact source symbol not verified")
                result = evaluate(snapshot)
                out = root / key
                manifest = write_snapshot(snapshot, out)
                _save(out / "comparative_results.json", result)
                item = {
                    "symbol": symbol, "interval": interval, "status": "COMPLETE",
                    "manifest_sha256": manifest["sha256"], "result": result,
                }
            except Exception as exc:
                item["error_class"] = type(exc).__name__
            entries.append(item)
            print(json.dumps({"case": key, "status": item["status"],
                              "late30_3x": {
                                  name: item["result"]["windows"]["late30"][name]
                                  ["stress3x"]["net_return_pct"]
                                  for name in NAMES
                              } if item["status"] == "COMPLETE" else None},
                             sort_keys=True), flush=True)
    if len(entries) != 12:
        raise AssertionError("frozen denominator changed")
    summary = {
        "schema": "econith-classical-quant-research-v1",
        "config_sha256": config_hash,
        "verdict": PASS if all(x["status"] == "COMPLETE" for x in entries)
                   else INCOMPLETE,
        "expected_market_timeframe_cases": 12,
        "completed_market_timeframe_cases": sum(x["status"] == "COMPLETE" for x in entries),
        "expected_strategy_market_timeframe_cases": 60,
        "strategies": list(NAMES), "frictions": ["base", "stress3x", "stress5x"],
        "test_windows": ["full", "late30"],
        "source_previously_inspected": True,
        "outer_holdout_untouched": False,
        "prospective_profit_proven": False,
        "capital_readiness": REJECT,
        "real_exchange_orders": 0,
        "results": entries,
    }
    _save(root / "unfiltered_challengers.json", summary)
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--simulation-only", action="store_true")
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args(argv)
    if not a.allow_public_market_data or not a.simulation_only:
        p.error("explicitly opt in to public Spot only + virtual money")
    try:
        result = run_all(a.output_dir)
    except (ValueError, TypeError, OSError, RuntimeError, KeyError) as exc:
        print(json.dumps({"verdict": INCOMPLETE, "error_class": type(exc).__name__,
                          "real_exchange_orders": 0}), flush=True)
        return 2
    s = {k: v for k, v in result.items() if k != "results"}
    for window in ("late30",):
        s[window + "_strategy_3x"] = {
            name: {
                "positive_cases": sum(
                    e["result"]["windows"][window][name]["stress3x"]["beats_cash"]
                    for e in result["results"] if e["status"] == "COMPLETE"),
                "beats_matched_buyhold_cases": sum(
                    e["result"]["windows"][window][name]["stress3x"]["beats_matched_buyhold"]
                    for e in result["results"] if e["status"] == "COMPLETE"),
                "mean_net_return_pct": round(
                    sum(e["result"]["windows"][window][name]["stress3x"]["net_return_pct"]
                        for e in result["results"] if e["status"] == "COMPLETE") /
                    max(1, result["completed_market_timeframe_cases"]), 6),
                "nonzero_trade_cases": sum(
                    e["result"]["windows"][window][name]["stress3x"]["simulated_fills"] > 0
                    for e in result["results"] if e["status"] == "COMPLETE"),
            } for name in NAMES
        }
    print(json.dumps(s, sort_keys=True, allow_nan=False), flush=True)
    return 0 if result["verdict"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
