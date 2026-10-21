"""ECONITH one-command research SPOT PAPER replay, single pair, NO live orders.

One public Binance Spot snapshot -> independently reconciled next-bar fills ->
normal and 3x friction comparison. This does not maintain forward positions,
prove alpha or interact with any exchange account. Not a live bot.

Example:
  python -m scripts.paper_trade_quickstart --symbol BTCUSDT \
    --capital 100 --interval 4h --candles 500 \
    --output-dir /absolute/new/private-paper-001
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
from typing import Callable

from econith.quant.paper_audit import audit_paper_report
from econith.quant.paper_v1 import (
    PaperConfig, SymbolRules, replay_spot, validated_candles,
)
from econith.quant.public_binance_data import (
    capture_spot_hourly, load_snapshot, write_snapshot,
)

INTERVALS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
PASS = "SIMULATION_REPLAY_COMPLETE_NOT_LIVE_READY"
CAPITAL_STATUS = "REAL_CAPITAL_NOT_AUTHORIZED"
_MAX_SNAPSHOT = 1_000


def _params(symbol: str, capital: float, interval: str,
            candles: int, output: Path) -> None:
    if (type(symbol) is not str or re.fullmatch(r"[A-Z0-9]{5,20}", symbol) is None
        or symbol in ("DOGE", "DOG", "USDT")
        or not symbol.endswith("USDT")
        or interval not in INTERVALS
        or type(candles) is not int or not 300 <= candles <= _MAX_SNAPSHOT
        or type(capital) not in (int, float) or not math.isfinite(capital)
        or not 10 <= capital <= 1000
        or not isinstance(output, Path) or not output.is_absolute()
        or output != output.resolve() or output.exists()
        or output.parent.is_symlink() or not output.parent.is_dir()):
        raise ValueError("unsafe single-pair public paper-trade scope")


def _write_once(dest: Path, payload: dict) -> None:
    if dest.exists() or dest.is_symlink():
        raise ValueError("paper evidence output exists")
    raw = (json.dumps(payload, sort_keys=True, indent=2,
                      allow_nan=False) + "\n").encode("utf-8")
    with dest.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _simulate(snapshot: dict, *, capital: float, stress: int) -> dict:
    if stress not in (1, 3):
        raise ValueError("predeclared 1x/3x friction only")
    interval = snapshot["timeframe"]
    if interval not in INTERVALS or len(snapshot["market"]) != 1:
        raise ValueError("paper engine expects one fully closed Spot pair")
    symbol = next(iter(snapshot["market"]))
    if set(snapshot["rules"]) != {symbol}:
        raise ValueError("symbol rules mismatch")
    market = validated_candles(
        snapshot["market"][symbol], timeframe_ms=INTERVALS[interval],
        asof_ms=snapshot["asof_ms"],
    )
    cfg = PaperConfig(
        initial_cash=capital, timeframe_ms=INTERVALS[interval],
        lookback=24, entry_threshold=0.01,
        max_asset_weight=0.2, max_total_weight=0.2,
        fee_bps=10 * stress, slippage_bps=2 * stress,
        half_spread_bps=1 * stress, max_drawdown=0.15,
    )
    report = replay_spot(
        {symbol: market},
        {symbol: SymbolRules(**snapshot["rules"][symbol])}, cfg,
    )
    raw = report.to_dict()
    ledger = audit_paper_report(raw)
    if (ledger["status"] != "SIMULATED_LEDGER_RECONCILED"
        or ledger["final_equity"] != report.final_equity
        or ledger["total_net_pnl"] != round(
            report.final_equity - capital, 8
        )):
        raise ValueError("paper cash, fills or realized PnL did not reconcile")
    return {
        "config": {
            "lookback": 24, "entry_threshold": 0.01,
            "max_asset_weight": 0.2, "max_drawdown": 0.15,
            "fee_bps": 10 * stress, "slippage_bps": 2 * stress,
            "half_spread_bps": 1 * stress,
            "fill_rule": "previous closed candle signal, next historical open fill",
        },
        "report": raw,
        "audit": ledger,
        "summary": {
            "equity_usdt": round(report.final_equity, 8),
            "net_pnl_usdt": round(report.final_equity - capital, 8),
            "return_pct": round(report.net_return * 100, 6),
            "max_drawdown_pct": round(report.max_drawdown * 100, 6),
            "simulated_fill_count": len(report.fills),
            "fee_usdt": ledger["explicit_fees_paid"],
            "drawdown_halted": report.halted,
        },
    }


def run_one(symbol: str, *, capital: float, interval: str,
            candles: int, output: Path,
            capture: Callable = capture_spot_hourly) -> dict:
    """Single capture and two independent fee-stress replays. No trade loop."""
    _params(symbol, capital, interval, candles, output)
    # The captured symbol must really exist on *this* public Spot venue;
    # e.g. DOG (Rune) is NOT silently substituted by DOGE or another DOG.
    snapshot = capture([symbol], limit=candles, interval=interval)
    if (snapshot.get("source") != "binance_spot_public"
        or snapshot.get("symbols") != [symbol]
        or set(snapshot.get("market", {})) != {symbol}
        or set(snapshot.get("rules", {})) != {symbol}
        or snapshot.get("timeframe") != interval):
        raise ValueError("untrusted or replaced pair/source snapshot")
    # Avoid creating any output until both ledgers agree (fail closed).
    normal = _simulate(snapshot, capital=capital, stress=1)
    stressed = _simulate(snapshot, capital=capital, stress=3)
    times = snapshot["market"][symbol]
    if len(times) < 300:
        raise ValueError("insufficient closed bars for a finite candidate")
    # A positive historical replay even after stressed fees is not a
    # forward paper-trading result, eligibility or an order authorization.
    status = {
        "schema": "econith-single-spot-paper-quickstart-v1",
        "verdict": PASS,
        "capital_readiness": CAPITAL_STATUS,
        "execution_mode": "SIMULATION_HISTORICAL_REPLAY_ONLY",
        "historical_only_not_prospective": True,
        "symbol": symbol, "venue": "Binance public Spot OHLCV",
        "timeframe": interval, "closed_candles": len(times),
        "first_open_ms": int(times[0][0]),
        "last_closed_ms": int(times[-1][6]),
        "starting_virtual_usdt": capital,
        "base": normal["summary"],
        "cost_stress_3x": stressed["summary"],
        "fragile_after_cost_stress": (
            stressed["summary"]["net_pnl_usdt"] <= 0
            or normal["summary"]["net_pnl_usdt"] <= 0
        ),
        "forecast_accuracy_proven": False,
        "prospective_live_profit_proven": False,
        "order_endpoints_available": False,
        "real_exchange_orders": 0,
        "warning": (
            "Past simulated next-open fills are not reproducible forward "
            "executions. Fee/spread/slippage are assumptions. "
            "Do not deposit capital or auto-order based on this report."
        ),
    }
    output.mkdir(mode=0o700)
    try:
        manifest = write_snapshot(snapshot, output)
        _write_once(output / "base_replay.json", normal)
        _write_once(output / "stress_3x_replay.json", stressed)
        status["snapshot_sha256"] = manifest["sha256"]
        _write_once(output / "summary.json", status)
    except BaseException:
        # Preserve partial private output so a failed run cannot fabricate
        # a successful summary. Never delete or replace original captures.
        raise
    return status


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--symbol", default="BTCUSDT",
                   help="exact public Spot pair; DOG Rune != DOGE")
    p.add_argument("--capital", type=float, default=100.,
                   help="virtual USDT only (10..1000)")
    p.add_argument("--interval", choices=tuple(INTERVALS), default="4h")
    p.add_argument("--candles", type=int, default=500)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--simulation-only", action="store_true")
    args = p.parse_args(argv)
    if not (args.allow_public_market_data and args.simulation_only):
        p.error("explicit PUBLIC_DATA + SIMULATION only consent required")
    try:
        result = run_one(
            args.symbol, capital=args.capital, interval=args.interval,
            candles=args.candles, output=args.output_dir,
        )
    except (ValueError, OSError, RuntimeError, TypeError, KeyError,
            IndexError) as exc:
        print(json.dumps({
            "verdict": "REFUSED_PAPER_EVIDENCE",
            "failure_class": type(exc).__name__,
            "real_exchange_orders": 0,
            "capital_readiness": CAPITAL_STATUS,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
