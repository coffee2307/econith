"""Offline demo-capital sweep on a verified, read-only Binance Spot snapshot.

Runs 10/100/1000 USDT capital and base/3x hypothetical execution friction.
All orders are synthetic next-open replays. Nothing connects to an exchange
account or certifies profitability.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from econith.quant.paper_audit import audit_paper_report
from econith.quant.paper_v1 import PaperConfig, SymbolRules, replay_spot, validated_candles
from econith.quant.public_binance_data import load_snapshot

PERIODS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def sweep_capitals(snapshot: dict, capitals: tuple[float, ...] = (10, 100, 1000)) -> dict:
    interval = str(snapshot["timeframe"])
    if interval not in PERIODS:
        raise ValueError("unsupported snapshot interval")
    timeframe_ms = PERIODS[interval]
    asof = int(snapshot["asof_ms"])
    market = {
        symbol: validated_candles(rows, timeframe_ms=timeframe_ms, asof_ms=asof)
        for symbol, rows in snapshot["market"].items()
    }
    rules = {symbol: SymbolRules(**info) for symbol, info in snapshot["rules"].items()}
    if not market or market.keys() != rules.keys():
        raise ValueError("snapshot market and rule sets mismatch")
    output = []
    for capital in capitals:
        for label, multiplier in (("base", 1), ("stress_3x", 3)):
            cfg = PaperConfig(
                initial_cash=capital, timeframe_ms=timeframe_ms,
                fee_bps=10 * multiplier,
                slippage_bps=2 * multiplier,
                half_spread_bps=1 * multiplier,
            )
            sim = replay_spot(market, rules, cfg)
            ledger = audit_paper_report(sim.to_dict())
            output.append({
                "capital": capital,
                "cost_scenario": label,
                "starting_cash": capital,
                "final_equity": sim.final_equity,
                "return_pct": round(sim.net_return * 100, 6),
                "drawdown_pct": round(sim.max_drawdown * 100, 6),
                "fill_count": len(sim.fills),
                "halted": sim.halted,
                "realized_net_pnl": ledger["realized_net_pnl"],
                "unrealized_net_pnl": ledger["unrealized_net_pnl"],
                "explicit_fees_paid": ledger["explicit_fees_paid"],
                "reconciliation_status": ledger["status"],
                "reproduction_eligible": False,
                "no_valid_orders": len(sim.fills) == 0,
            })
    return {
        "source": "SHA256_VERIFIED_OFFLINE_SPOT_SNAPSHOT",
        "symbol_count": len(market),
        "symbols": sorted(market),
        "timeframe": interval,
        "bar_count": len(next(iter(market.values()))),
        "capital_usdt": list(capitals),
        "scenarios": output,
        "real_exchange_orders": 0,
        "high_confidence_profitable": False,
        "readiness": "NOT_READY_FOR_CAPITAL_OR_AUTONOMOUS_DEMO",
        "notes": [
            "Cost figures are assumptions, not actual Binance fills.",
            "Small balances may produce zero trades due to 20% allocation and min notional.",
            "Paper fill reconciliation does not prove predictive alpha.",
            "Trading bots are not permitted to turn these simulated profits into agent rewards.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline ECONITH demo-capital sweep")
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.resolve() in {
        (args.snapshot_dir / "market.json").resolve(),
        (args.snapshot_dir / "rules.json").resolve(),
        (args.snapshot_dir / "manifest.json").resolve(),
    }:
        parser.error("output must not overwrite source snapshots")
    result = sweep_capitals(load_snapshot(args.snapshot_dir))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(result["readiness"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
