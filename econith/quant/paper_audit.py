"""Reconcile ECONITH offline spot paper fills to cash, position and net PnL.

Audited simulated PnL is NOT independently exchange-verified profit and must
never activate evolution/reproduction or capital-bearing execution.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


class PaperAuditError(ValueError):
    """Fail-closed on inconsistent or unverifiable paper accounting."""


def _finite(value: object, label: str) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PaperAuditError(f"{label} is not numeric") from exc
    if not math.isfinite(x):
        raise PaperAuditError(f"{label} must be finite")
    return x


def _close(a: float, b: float, label: str) -> None:
    if not math.isclose(a, b, abs_tol=1e-5, rel_tol=1e-8):
        raise PaperAuditError(f"{label} does not reconcile: {a} vs {b}")


def audit_paper_report(report: dict) -> dict:
    """Reconstruct cash, FIFO-free weighted-average cost basis and sale PnL.

    This requires a SIMULATION-only report. Every listed fill must reconcile
    with the dated cash ledger, final holdings, equity and drawdown. Buying
    commissions increase cost basis; selling commissions reduce proceeds.
    """
    if not isinstance(report, dict):
        raise PaperAuditError("paper report must be a dict")
    initial = _finite(report.get("initial_cash"), "initial_cash")
    if initial <= 0:
        raise PaperAuditError("initial_cash must be positive")
    curve = report.get("equity_curve")
    fills = report.get("fills")
    positions = report.get("last_positions")
    if not isinstance(curve, list) or len(curve) < 2:
        raise PaperAuditError("missing portfolio equity curve")
    if not isinstance(fills, list) or not isinstance(positions, dict):
        raise PaperAuditError("missing fills or positions")

    snapshots: dict[int, dict] = {}
    prev_t = -1
    for row in curve:
        t = int(row["timestamp_ms"])
        if t < 0 or t <= prev_t:
            raise PaperAuditError("non-monotonic equity curve")
        eq = _finite(row["equity"], "equity")
        cash = _finite(row["cash"], "cash")
        exposure = _finite(row["exposure"], "exposure")
        if eq <= 0 or cash < -1e-5 or exposure < -1e-5:
            raise PaperAuditError("invalid equity/cash/exposure")
        _close(eq, cash + exposure, "equity equals cash plus exposure")
        snapshots[t] = row
        prev_t = t

    cash = initial
    shares: dict[str, float] = {}
    costs: dict[str, float] = {}
    gross_fees = 0.0
    turnover = 0.0
    realized = 0.0
    sales = 0
    winning_sales = 0
    trades_by_time: dict[int, list] = {}
    prev_fill_t = -1
    for fill in fills:
        if fill.get("execution_mode") != "SIMULATION":
            raise PaperAuditError("non-simulation fill in paper report")
        t = int(fill["timestamp_ms"])
        if t < prev_fill_t or t not in snapshots:
            raise PaperAuditError("fill outside the reconciled time series")
        prev_fill_t = t
        trades_by_time.setdefault(t, []).append(fill)

    for t, row in snapshots.items():
        for fill in trades_by_time.get(t, []):
            symbol = str(fill.get("symbol", "")).strip()
            if not symbol or symbol not in positions:
                raise PaperAuditError("fill symbol missing from final position ledger")
            side = str(fill.get("side", ""))
            qty = _finite(fill.get("quantity"), "quantity")
            price = _finite(fill.get("price"), "price")
            fee = _finite(fill.get("fee"), "fee")
            if qty <= 0 or price <= 0 or fee < 0 or fee >= qty * price:
                raise PaperAuditError("invalid fill economics")
            traded = qty * price
            gross_fees += fee
            turnover += traded
            old_qty = shares.get(symbol, 0.0)
            old_cost = costs.get(symbol, 0.0)
            if side == "BUY":
                cash -= traded + fee
                shares[symbol] = old_qty + qty
                costs[symbol] = old_cost + traded + fee
            elif side == "SELL":
                if old_qty + 1e-8 < qty or old_qty <= 0:
                    raise PaperAuditError("short sale or duplicate sell fill")
                matched = min(qty, old_qty)
                basis = old_cost * (matched / old_qty)
                proceeds = traded - fee
                gain = proceeds - basis
                realized += gain
                sales += 1
                winning_sales += int(gain > 0)
                cash += proceeds
                shares[symbol] = max(0.0, old_qty - matched)
                costs[symbol] = max(0.0, old_cost - basis)
            else:
                raise PaperAuditError("unsupported fill direction")
            if cash < -1e-5:
                raise PaperAuditError("cash debit exceeds available capital")
        _close(cash, _finite(row["cash"], "cash"), f"cash at {t}")

    for symbol, value in positions.items():
        q = _finite(value, f"{symbol} final quantity")
        if q < -1e-9:
            raise PaperAuditError("negative position")
        _close(shares.get(symbol, 0.0), q, f"{symbol} final holdings")
    final = _finite(report.get("final_equity"), "final_equity")
    _close(final, _finite(curve[-1]["equity"], "ending equity"), "final equity")
    _close(final / initial - 1, _finite(report.get("net_return"), "net_return"),
           "net return")
    peak = initial
    largest_dd = 0.0
    for row in curve:
        v = _finite(row["equity"], "equity")
        peak = max(peak, v)
        largest_dd = max(largest_dd, 1 - v / peak)
    _close(largest_dd, _finite(report.get("max_drawdown"), "drawdown"),
           "maximum drawdown")

    unclosed_cost = sum(costs.values())
    unrealized = final - cash - unclosed_cost
    _close(realized + unrealized, final - initial, "PnL decomposition")
    return {
        "status": "SIMULATED_LEDGER_RECONCILED",
        "initial_cash": initial,
        "final_equity": final,
        "final_cash": cash,
        "realized_net_pnl": round(realized, 8),
        "unrealized_net_pnl": round(unrealized, 8),
        "total_net_pnl": round(final - initial, 8),
        "explicit_fees_paid": round(gross_fees, 8),
        "gross_notional_turnover": round(turnover, 8),
        "closed_sell_legs": sales,
        "winning_sell_legs": winning_sales,
        "final_positions": {k: round(v, 12) for k, v in shares.items()},
        "evolution_reward_eligible": False,
        "reason": "historical/simulated fills do not prove externally verified profit",
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Audit offline ECONITH paper ledger")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must differ")
    output = audit_paper_report(json.loads(args.input.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(output["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
