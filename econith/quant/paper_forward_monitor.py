"""Forward paper portfolio observability from *new* public bid/ask quotes.

Implements deterministic control patterns inspired by Freqtrade protections
and Hummingbot controllers, without copying proprietary/GPL strategy logic.
No order execution, accounts, leverage, PnL prediction or live keys.
A cash and matched first-quote 20%-capital passive benchmark are kept.
Timestamp is local receipt, NEVER certified exchange event time.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time

from econith.quant.forward_quote_dryrun import (
    PaperRefused, FEE_BPS, SLIPPAGE_BPS, HALT_DRAWDOWN,
    _account, _db_connect, _quote_strict,
)
from econith.quant.paper_v1 import SymbolRules

TABLE = """
CREATE TABLE IF NOT EXISTS forward_marks (
    seq INTEGER PRIMARY KEY,
    received_local_ms INTEGER NOT NULL UNIQUE,
    bid REAL NOT NULL,
    ask REAL NOT NULL,
    strategy_cash REAL NOT NULL,
    strategy_qty REAL NOT NULL,
    strategy_equity REAL NOT NULL,
    passive_cash REAL NOT NULL,
    passive_qty REAL NOT NULL,
    passive_equity REAL NOT NULL,
    strategy_hwm REAL NOT NULL,
    dd REAL NOT NULL,
    risk_halted INTEGER NOT NULL,
    prev_sha256 TEXT NOT NULL,
    row_sha256 TEXT NOT NULL
)
"""
GENESIS = "0" * 64


def _hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _mark_data(row) -> dict:
    cols = ("seq", "received_local_ms", "bid", "ask", "strategy_cash",
            "strategy_qty", "strategy_equity", "passive_cash",
            "passive_qty", "passive_equity", "strategy_hwm", "dd",
            "risk_halted", "prev_sha256")
    return dict(zip(cols, row[:-1]))


def _audit(db) -> list[dict]:
    if db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='forward_marks'").fetchone() is None:
        return []
    rows = db.execute(
        """SELECT seq, received_local_ms, bid, ask, strategy_cash,
                  strategy_qty, strategy_equity, passive_cash, passive_qty,
                  passive_equity, strategy_hwm, dd, risk_halted,
                  prev_sha256, row_sha256
           FROM forward_marks ORDER BY seq"""
    ).fetchall()
    expected_seq, previous, last_time = 1, GENESIS, 0
    result = []
    for row in rows:
        record = _mark_data(row)
        if (record["seq"] != expected_seq
            or record["received_local_ms"] <= last_time
            or record["prev_sha256"] != previous
            or _hash(record) != row[-1]):
            raise PaperRefused("quote mark hash chain or chronology invalid")
        for field in ("bid", "ask", "strategy_cash", "strategy_qty",
                      "strategy_equity", "passive_cash", "passive_qty",
                      "passive_equity", "strategy_hwm", "dd"):
            val = record[field]
            if not isinstance(val, (float, int)) or not math.isfinite(val):
                raise PaperRefused("nonfinite recorded quote mark")
        if (not 0 < record["bid"] <= record["ask"]
            or record["strategy_cash"] < -1e-6
            or record["strategy_qty"] < -1e-9
            or record["passive_cash"] < -1e-6
            or record["passive_qty"] < -1e-9
            or record["strategy_equity"] <= 0
            or abs(record["strategy_equity"] - (
                record["strategy_cash"] + record["strategy_qty"] * record["bid"]
            )) > 1e-5
            or abs(record["passive_equity"] - (
                record["passive_cash"] + record["passive_qty"] * record["bid"]
            )) > 1e-5
            or record["risk_halted"] not in (0, 1)
            or not 0 <= record["dd"] < 1):
            raise PaperRefused("quote mark economic or risk invariants invalid")
        if result:
            last = result[-1]
            if (record["strategy_hwm"] < last["strategy_hwm"] - 1e-6
                or abs(record["passive_cash"] - last["passive_cash"]) > 1e-6
                or abs(record["passive_qty"] - last["passive_qty"]) > 1e-12):
                raise PaperRefused("quote mark highwater or passive benchmark regressed")
        previous, last_time = row[-1], record["received_local_ms"]
        expected_seq += 1
        result.append(record)
    return result


def append_mark(path: Path, quote: dict, rules: dict, *,
                received_local_ms: int | None = None) -> dict:
    """Mark holdings on a real *later observed* quote; no hypothetical fill.

    Cash control = initial virtual USDT. Passive control is single 20%-capital
    buy at first genuine quote *after monitoring was enabled*, not an
    invented historical entry. If the account was trading before monitoring,
    the comparison interval starts at its first mark (and is labelled).
    """
    now = (time.time_ns() // 1_000_000
           if received_local_ms is None else received_local_ms)
    if type(now) is not int or now <= 0:
        raise PaperRefused("invalid local receipt timestamp")
    with _db_connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            account = _account(db)
            bid, ask = _quote_strict(quote, account["symbol"])
            if type(rules) is not dict or set(rules) != {"step_size", "min_notional"}:
                raise PaperRefused("actual public venue lot filters required")
            lot = SymbolRules(**rules)
            db.execute(TABLE)
            previous = _audit(db)
            if previous and now <= previous[-1]["received_local_ms"]:
                raise PaperRefused("market timestamp must increase for each quote mark")
            # Defensive source sanity; no unobserved market movement is filled
            # automatically just because a quote is very far away.
            last_close = account["last_close_price"]
            if last_close is not None and (
                abs(bid / last_close - 1) > .20
                or abs(ask / last_close - 1) > .20
            ):
                raise PaperRefused("quote deviates >20pct from last closed bar")
            cash, qty = account["cash"], account["qty"]
            eq = cash + qty * bid
            if not math.isfinite(eq) or eq <= 0:
                raise PaperRefused("invalid marked equity")
            if not previous:
                # Passive buy uses the first observed ask, slippage and fees,
                # with venue quantity step/minimum notional.
                buy = ask * (1 + SLIPPAGE_BPS / 10000.)
                passive_qty = lot.floor_qty(
                    (.20 * account["initial"]) /
                    (buy * (1 + FEE_BPS / 10000.))
                )
                if passive_qty * buy < lot.min_notional:
                    passive_qty = 0.
                passive_cash = account["initial"] - passive_qty * buy * (
                    1 + FEE_BPS / 10000.
                )
                if passive_cash < -1e-7:
                    raise PaperRefused("benchmark exceeds virtual initial cash")
                peak = max(account["highwater"], eq)
                chain = GENESIS
            else:
                prev = previous[-1]
                passive_cash, passive_qty = prev["passive_cash"], prev["passive_qty"]
                peak = max(account["highwater"], prev["strategy_hwm"], eq)
                chain = db.execute(
                    "SELECT row_sha256 FROM forward_marks ORDER BY seq DESC LIMIT 1"
                ).fetchone()[0]
            dd = 1. - eq / peak
            halted = bool(account["halted"]) or dd >= HALT_DRAWDOWN
            entry = {
                "seq": len(previous) + 1,
                "received_local_ms": now, "bid": bid, "ask": ask,
                "strategy_cash": cash, "strategy_qty": qty,
                "strategy_equity": eq, "passive_cash": passive_cash,
                "passive_qty": passive_qty,
                "passive_equity": passive_cash + passive_qty * bid,
                "strategy_hwm": peak, "dd": dd,
                "risk_halted": int(halted), "prev_sha256": chain,
            }
            digest = _hash(entry)
            db.execute(
                """INSERT INTO forward_marks VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(entry.values()) + (digest,),
            )
            db.execute("UPDATE account SET highwater=?,halted=? WHERE id=1",
                       (peak, int(halted)))
            db.execute("COMMIT")
            return {
                "status": "PUBLIC_QUOTE_PAPER_MARK_RECORDED",
                "received_local_ms": now,
                "local_clock_exchange_attested": False,
                "strategy_equity_usdt": round(eq, 8),
                "strategy_pnl_usdt": round(eq - account["initial"], 8),
                "strategy_return_pct": round(
                    100 * (eq / account["initial"] - 1), 6),
                "passive_20pct_equity_usdt": round(entry["passive_equity"], 8),
                "passive_20pct_return_pct": round(
                    100 * (entry["passive_equity"] / account["initial"] - 1), 6),
                "cash_baseline_return_pct": 0.,
                "mark_sequence": entry["seq"],
                "mark_sha256": digest,
                "risk_halted": halted,
                "real_orders": 0,
                "profit_proven": False,
            }
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


def report(path: Path) -> dict:
    """Only committed, re-audited quote marks are performance evidence."""
    with _db_connect(path) as db:
        a = _account(db)
        marks = _audit(db)
        result = {
            "schema": "econith-forward-quote-pnl-report-v1",
            "symbol": a["symbol"], "strategy": a["strategy"],
            "interval": a["interval"], "start_virtual_cash_usdt": a["initial"],
            "last_closed_bar_ms": a["last_close_ms"],
            "strategy_virtual_cash_usdt": round(a["cash"], 8),
            "strategy_virtual_quantity": a["qty"],
            "verified_quote_marks": len(marks),
            "risk_halted": bool(a["halted"]),
            "virtual_only": True,
            "real_orders": 0, "real_money_authorized": False,
            "prospective_profit_proven": False,
            "local_quote_time_not_exchange_attested": True,
            "benchmark_starts_at_first_independently_observed_quote": True,
        }
        if not marks:
            result["performance"] = "UNKNOWN_NO_FORWARD_MARKS"
            return result
        last = marks[-1]
        if (abs(last["strategy_cash"] - a["cash"]) > 1e-6
            or abs(last["strategy_qty"] - a["qty"]) > 1e-9):
            # A fill after last mark must be observed again before reporting.
            result["performance"] = "STALE_MARK_REQUIRES_FRESH_QUOTE"
            return result
        first = marks[0]
        result.update({
            "performance": "OBSERVED_PUBLIC_QUOTE_SIMULATED_NOT_REAL_PROFIT",
            "first_quote_local_ms": first["received_local_ms"],
            "last_quote_local_ms": last["received_local_ms"],
            "first_quote_asset_quantity": first["strategy_qty"],
            "starting_with_preexisting_virtual_position": first["strategy_qty"] > 0,
            "strategy_marked_equity_usdt": round(last["strategy_equity"], 8),
            "strategy_total_return_pct": round(
                100 * (last["strategy_equity"] / a["initial"] - 1), 6),
            "passive_20pct_equity_usdt": round(last["passive_equity"], 8),
            "passive_20pct_total_return_pct": round(
                100 * (last["passive_equity"] / a["initial"] - 1), 6),
            "excess_strategy_vs_passive_usdt": round(
                last["strategy_equity"] - last["passive_equity"], 8),
            "observed_max_drawdown_pct": round(
                max(x["dd"] for x in marks) * 100., 6),
            "cash_control_return_pct": 0.,
            "latest_mark_sha256": _hash(last),
            "note": "Hypothetical taker fills, not exchange-authenticated executions; no continuous duration claim.",
        })
        return result
