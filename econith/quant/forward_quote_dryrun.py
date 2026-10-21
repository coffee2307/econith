"""Research-only forward Spot paper decisions and *post-signal* quote fills.

A small deliberate Freqtrade-like DRY-RUN core, not a licensed Freqtrade
code copy, exchange execution bridge, or TradingAgents LLM. A read-only
market-data adapter is the only network path. This unit does not auto-run.
It cannot execute trades, access accounts or certify real profit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time

from econith.quant.classical_challengers import NAMES, CANDIDATE_WARMUP, target
from econith.quant.paper_v1 import SymbolRules, validated_candles
from scripts.paper_trade_quickstart import _capture_exact_public_pair
from scripts.paper_unfiltered_matrix import PERIOD
from ci.phase1_spot_multiasset_ohlcv import request_public

SCHEMA = "econith-research-forward-paper-v1"
MAX_SPREAD_BPS = 100.0
MAX_QUOTE_DELAY_MS = 60000
MAX_PRICE_JUMP = 0.20
FEE_BPS = 10.
SLIPPAGE_BPS = 2.
HALT_DRAWDOWN = .15


class PaperRefused(ValueError):
    pass


def _sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _db_connect(path: Path, *, create: bool = False) -> sqlite3.Connection:
    if (not path.is_absolute() or path.is_symlink() or path != path.resolve()
        or not path.parent.is_dir() or path.parent.is_symlink()):
        raise PaperRefused("new or existing absolute non-aliased SQLite research path required")
    if create and path.exists():
        raise PaperRefused("research ledger already exists; never overwrite")
    if not create and (not path.is_file() or not path.exists()):
        raise PaperRefused("initialize research ledger first")
    mode = "rwc" if create else "rw"
    db = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True,
                         isolation_level=None, timeout=3)
    db.execute("PRAGMA busy_timeout=3000")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _init(db: sqlite3.Connection, symbol: str, strategy: str,
          interval: str, capital: float) -> None:
    if (type(symbol) is not str or not symbol.endswith("USDT") or
        not symbol.isascii() or not symbol.isalnum() or
        len(symbol) > 20 or strategy not in NAMES or
        interval not in PERIOD or type(capital) not in (int, float) or
        not math.isfinite(capital) or not 10 <= capital <= 1000):
        raise PaperRefused("only bounded public Spot single-symbol virtual paper")
    try:
        db.executescript("""
            CREATE TABLE account (
              id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL,
              symbol TEXT NOT NULL, strategy TEXT NOT NULL,
              interval TEXT NOT NULL, cash REAL NOT NULL, qty REAL NOT NULL,
              initial REAL NOT NULL, highwater REAL NOT NULL,
              halted INTEGER NOT NULL CHECK(halted IN (0,1)),
              last_close_ms INTEGER, last_close_price REAL,
              history_json TEXT NOT NULL, history_sha256 TEXT NOT NULL
            );
            CREATE TABLE decisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              last_closed_bar_ms INTEGER NOT NULL UNIQUE,
              recorded_local_ms INTEGER NOT NULL, history_sha256 TEXT NOT NULL,
              requested_weight REAL NOT NULL, status TEXT NOT NULL,
              note TEXT NOT NULL
            );
            CREATE TABLE paper_fills (
              decision_id INTEGER PRIMARY KEY REFERENCES decisions(id),
              received_quote_ms INTEGER NOT NULL, side TEXT NOT NULL,
              quantity REAL NOT NULL, execution_price REAL NOT NULL,
              fee REAL NOT NULL, bid REAL NOT NULL, ask REAL NOT NULL,
              post_cash REAL NOT NULL, post_quantity REAL NOT NULL
            );
        """)
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO account VALUES
                   (1,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                   (SCHEMA, symbol, strategy, interval, float(capital), 0.,
                    float(capital), float(capital), 0, None, None,
                    "[]", _sha([])))
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise


def initialize(path: Path, *, symbol="BTCUSDT",
               strategy="trend_vol_v1", interval="1h",
               capital=100.) -> dict:
    # Validate BEFORE opening the SQLite file: invalid symbol/strategy
    # cannot leave a false partially initialized research ledger.
    if (type(symbol) is not str or not symbol.endswith("USDT") or
        not symbol.isascii() or not symbol.isalnum() or len(symbol) > 20
        or strategy not in NAMES or interval not in PERIOD
        or type(capital) not in (int, float) or not math.isfinite(capital)
        or not 10 <= capital <= 1000):
        raise PaperRefused("invalid one-pair, virtual-capital research scope")
    with _db_connect(path, create=True) as db:
        _init(db, symbol, strategy, interval, capital)
    return {"schema": SCHEMA, "status": "RESEARCH_LEDGER_INITIALIZED",
            "symbol": symbol, "strategy": strategy, "interval": interval,
            "virtual_cash": capital, "real_orders": 0}


def _account(db):
    row = db.execute("SELECT * FROM account WHERE id=1").fetchone()
    if row is None or row[1] != SCHEMA:
        raise PaperRefused("research ledger schema mismatch")
    fields = ("id", "schema", "symbol", "strategy", "interval", "cash",
              "qty", "initial", "highwater", "halted", "last_close_ms",
              "last_close_price", "history_json", "history_sha256")
    result = dict(zip(fields, row))
    if (_sha(json.loads(result["history_json"])) != result["history_sha256"]
        or result["cash"] < -1e-8 or result["qty"] < -1e-12):
        raise PaperRefused("research journal history/cash integrity invalid")
    return result


def observe(path: Path, snapshot: dict, *, local_ms: int | None = None) -> dict:
    """Prepare exactly one new closed-bar target, never backfill pretend decisions.

    A runner may bootstrap past bars for indicators, but it generates a
    decision at its *actual observation time*, never retrospectively.
    """
    now = (time.time_ns() // 1_000_000 if local_ms is None else local_ms)
    if type(now) is not int or now <= 0:
        raise PaperRefused("invalid local observation timestamp")
    with _db_connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            a = _account(db)
            if (snapshot.get("source") != "binance_spot_public"
                or snapshot.get("symbols") != [a["symbol"]]
                or snapshot.get("timeframe") != a["interval"]
                or set(snapshot.get("market", {})) != {a["symbol"]}
                or set(snapshot.get("rules", {})) != {a["symbol"]}):
                raise PaperRefused("wrong public source identity or symbol")
            rows = snapshot["market"][a["symbol"]]
            bars = validated_candles(rows, timeframe_ms=PERIOD[a["interval"]],
                                     asof_ms=snapshot["asof_ms"])
            if len(bars) < CANDIDATE_WARMUP + 2:
                raise PaperRefused("insufficient strictly closed candle warmup")
            last = bars[-1]
            if (last.close_ms > now or now - last.close_ms >
                PERIOD[a["interval"]] + 30000):
                raise PaperRefused("last bar is future or stale at real observation")
            SymbolRules(**snapshot["rules"][a["symbol"]])
            originals = json.loads(a["history_json"])
            by_time = {x[0]: x for x in originals}
            seen = 0
            for row in rows:
                opened = int(row[0])
                if opened in by_time:
                    seen += 1
                    if _sha(list(row[:7])) != _sha(by_time[opened]):
                        raise PaperRefused("previously recorded market candle revised")
            if originals and seen < 99:
                raise PaperRefused("insufficient independently overlapping original candles")
            if a["last_close_ms"] is not None:
                if last.close_ms == a["last_close_ms"]:
                    db.execute("COMMIT")
                    return {"status": "IDEMPOTENT_SAME_BAR_NO_NEW_SIGNAL",
                            "real_orders": 0}
                if last.close_ms != a["last_close_ms"] + PERIOD[a["interval"]]:
                    raise PaperRefused("gap or skipped source time: do not fabricate paper fills")
                pending = db.execute(
                    "SELECT COUNT(*) FROM decisions WHERE status='PENDING_QUOTE'"
                ).fetchone()[0]
                if pending:
                    raise PaperRefused("previous decision pending: not silently overwritten")
            desired = target(a["strategy"], tuple(bars))
            if a["halted"]:
                desired = 0.
            compact = [list(r[:7]) for r in rows]
            # Store original time OHLC, close, volume and original bar end
            # used for price decisions: compare only matching normalized columns.
            h = _sha(compact)
            db.execute(
                """UPDATE account SET last_close_ms=?,last_close_price=?,
                   history_json=?, history_sha256=? WHERE id=1""",
                (last.close_ms, last.close, json.dumps(compact, separators=(",", ":")), h)
            )
            # A policy whose desired holding is already equal to current
            # holdings will still get a decision, but no venue order.
            db.execute(
                """INSERT INTO decisions
                   (last_closed_bar_ms,recorded_local_ms,history_sha256,
                    requested_weight,status,note)
                   VALUES (?,?,?,?,?,?)""",
                (last.close_ms, now, h, desired, "PENDING_QUOTE",
                 "market-only policy; awaits strictly later public bid/ask")
            )
            ident = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("COMMIT")
            return {"status": "SIGNAL_RECORDED_AWAITING_POST_SIGNAL_QUOTE",
                    "signal_id": ident, "last_closed_bar_ms": last.close_ms,
                    "recorded_local_ms": now, "desired_weight": desired,
                    "strategy": a["strategy"], "real_orders": 0}
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


def _quote_strict(quote: dict, symbol: str) -> tuple[float, float]:
    if (type(quote) is not dict or quote.get("symbol") != symbol or
        type(quote.get("bidPrice")) is not str or
        type(quote.get("askPrice")) is not str):
        raise PaperRefused("exact post-signal bid/ask symbol required")
    try:
        bid, ask = float(quote["bidPrice"]), float(quote["askPrice"])
    except ValueError as e:
        raise PaperRefused("invalid quote") from e
    if (not all(math.isfinite(x) and x > 0 for x in (bid, ask))
        or ask < bid or ((ask - bid) / ((ask + bid) / 2) * 10000 > MAX_SPREAD_BPS)):
        raise PaperRefused("crossed, nonfinite, zero or extremely wide market")
    return bid, ask


def fill(path: Path, quote: dict, rules: dict, *,
         quote_local_ms: int | None = None) -> dict:
    """Simulated immediate fill *after* observed target, with strict risk veto.

    Local quote receipt time is **not independently venue-attested**.
    No fabricated historical candle OPEN is used as an execution price.
    """
    now = time.time_ns() // 1_000_000 if quote_local_ms is None else quote_local_ms
    if type(now) is not int or now <= 0:
        raise PaperRefused("invalid local quote clock")
    with _db_connect(path) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            a = _account(db)
            bid, ask = _quote_strict(quote, a["symbol"])
            if (type(rules) is not dict or set(rules) !=
                {"step_size", "min_notional"}):
                raise PaperRefused("public venue exchange lot rules required")
            lot = SymbolRules(**rules)
            row = db.execute(
                """SELECT id, recorded_local_ms, requested_weight FROM decisions
                   WHERE status='PENDING_QUOTE' ORDER BY id LIMIT 1"""
            ).fetchone()
            if row is None:
                raise PaperRefused("no unresolved recorded signal; refuse speculative trade")
            decision_id, decided_ms, weight = row
            if not decided_ms < now <= decided_ms + MAX_QUOTE_DELAY_MS:
                raise PaperRefused("market quote stale, same-time or precedes signal")
            last_px = float(a["last_close_price"])
            if max(abs(bid / last_px - 1), abs(ask / last_px - 1)) > MAX_PRICE_JUMP:
                raise PaperRefused("post-signal price jumped >20%: risk veto")
            # Independent *current quote* mark. No high/low future knowledge.
            equity_before = a["cash"] + a["qty"] * bid
            peak = max(a["highwater"], equity_before)
            drawdown = 1 - equity_before / peak
            halted = bool(a["halted"]) or drawdown >= HALT_DRAWDOWN
            target_weight = 0. if halted else weight
            exposure = target_weight * equity_before
            want_qty = lot.floor_qty(exposure / ask)
            delta = want_qty - a["qty"]
            sign = "BUY" if delta > 0 else "SELL"
            trade_qty = lot.floor_qty(abs(delta))
            exec_px = (ask * (1 + SLIPPAGE_BPS / 10000.)
                       if sign == "BUY" else bid * (1 - SLIPPAGE_BPS / 10000.))
            notional = trade_qty * exec_px
            if trade_qty <= 0 or notional < lot.min_notional:
                status = "NO_FILL_SIZE_FILTER"
                trade_qty = 0.
            else:
                cost = notional * (1 + FEE_BPS / 10000.)
                if sign == "BUY":
                    affordable = lot.floor_qty(
                        max(0., a["cash"]) / (exec_px * (1 + FEE_BPS / 10000.))
                    )
                    trade_qty = min(trade_qty, affordable)
                    notional = trade_qty * exec_px
                if trade_qty <= 0 or notional < lot.min_notional:
                    status = "NO_FILL_INSUFFICIENT_CASH"
                else:
                    status = "SIMULATED_FILL_AT_POST_SIGNAL_QUOTE"
                    fee = notional * FEE_BPS / 10000.
                    after_cash = (a["cash"] - notional - fee if sign == "BUY"
                                  else a["cash"] + notional - fee)
                    after_qty = (a["qty"] + trade_qty if sign == "BUY"
                                 else a["qty"] - trade_qty)
                    if after_cash < -1e-6 or after_qty < -1e-9:
                        raise PaperRefused("paper asset or cash conservation failed")
                    db.execute(
                        """INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (decision_id, now, sign, trade_qty, exec_px, fee,
                         bid, ask, after_cash, after_qty)
                    )
                    db.execute(
                        "UPDATE account SET cash=?,qty=?,highwater=?,halted=? WHERE id=1",
                        (max(0., after_cash), max(0., after_qty),
                         peak, int(halted))
                    )
            if status != "SIMULATED_FILL_AT_POST_SIGNAL_QUOTE":
                db.execute("UPDATE account SET highwater=?,halted=? WHERE id=1",
                           (peak, int(halted)))
            db.execute("UPDATE decisions SET status=?,note=? WHERE id=?",
                       (status, "No real order: local post-signal quote only", decision_id))
            db.execute("COMMIT")
            return {"status": status, "signal_id": decision_id,
                    "quoted_at_local_ms": now, "local_clock_exchange_attested": False,
                    "bid": bid, "ask": ask, "spread_bps": round(
                        (ask - bid) / ((ask + bid) / 2) * 10000, 5),
                    "current_equity_usdt": round(equity_before, 8),
                    "current_drawdown": round(drawdown, 8),
                    "risk_halted": halted, "real_orders": 0}
        except BaseException:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise


def status(path: Path) -> dict:
    with _db_connect(path) as db:
        a = _account(db)
        d = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(status='PENDING_QUOTE'),0) FROM decisions"
        ).fetchone()
        n = db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
        return {
            "schema": SCHEMA, "symbol": a["symbol"], "strategy": a["strategy"],
            "interval": a["interval"], "virtual_cash_usdt": round(a["cash"], 8),
            "virtual_asset_quantity": a["qty"],
            "virtual_starting_usdt": a["initial"],
            "last_closed_bar_ms": a["last_close_ms"],
            "signal_count": d[0], "pending_signals": d[1],
            "simulated_fills": n, "halted": bool(a["halted"]),
            "execution_mode": "SIMULATION_PUBLIC_QUOTE_SHADOW_ONLY",
            "real_orders": 0, "capital_readiness": "REAL_CAPITAL_NOT_AUTHORIZED",
        }


def public_quote(symbol: str) -> dict:
    # GET-only, pinned public data-only URL. No account/order API surface.
    raw = request_public("/api/v3/ticker/bookTicker", {"symbol": symbol})
    return json.loads(raw)


def public_rules(symbol: str) -> dict:
    from econith.quant.public_binance_data import _rules
    payload = json.loads(request_public("/api/v3/exchangeInfo", {"symbol": symbol}))
    return _rules(payload, symbol)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--action", choices=("init", "observe", "fill", "status"), required=True)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--strategy", choices=NAMES, default="trend_vol_v1")
    p.add_argument("--interval", choices=tuple(PERIOD), default="1h")
    p.add_argument("--capital", type=float, default=100.)
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--simulation-only", action="store_true")
    a = p.parse_args(argv)
    if not a.simulation_only or (
        a.action in ("observe", "fill") and not a.allow_public_market_data
    ):
        p.error("only explicit SIMULATION and optional public read-only source permitted")
    try:
        if a.action == "init":
            result = initialize(a.ledger, symbol=a.symbol, strategy=a.strategy,
                                interval=a.interval, capital=a.capital)
        elif a.action == "observe":
            current = status(a.ledger)
            snap = _capture_exact_public_pair(
                [current["symbol"]], limit=200, interval=current["interval"]
            )
            result = observe(a.ledger, snap)
        elif a.action == "fill":
            current = status(a.ledger)
            result = fill(a.ledger, public_quote(current["symbol"]),
                          public_rules(current["symbol"]))
        else:
            result = status(a.ledger)
    except (ValueError, KeyError, TypeError, sqlite3.Error,
            OSError, AssertionError) as exc:
        print(json.dumps({"status": "REFUSED_PAPER_RESEARCH",
                          "failure_class": type(exc).__name__,
                          "capital_readiness": "REAL_CAPITAL_NOT_AUTHORIZED",
                          "real_orders": 0}))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
