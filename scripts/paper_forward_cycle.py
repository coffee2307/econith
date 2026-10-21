"""One repeatable PUBLIC-ONLY research paper cycle with safe restart semantics.

A Freqtrade-like foreground *controller* (NOT an exchange order executor)
with Hummingbot-like stages: market observer -> strategy -> quote risk veto
-> paper bookkeeping -> independent mark/PnL.  No background daemon,
secrets, live orders, real money, futures or automatic profit claims.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable

from econith.quant.forward_quote_dryrun import (
    MAX_QUOTE_DELAY_MS, PaperRefused, _db_connect, _account,
    fill, observe, public_quote, public_rules, status,
)
from econith.quant.paper_forward_monitor import append_mark, report
from scripts.paper_trade_quickstart import _capture_exact_public_pair


def _pending(path: Path):
    with _db_connect(path) as db:
        _account(db)
        return db.execute(
            """SELECT id, recorded_local_ms FROM decisions
               WHERE status='PENDING_QUOTE' ORDER BY id LIMIT 1"""
        ).fetchone()


def cycle(
    path: Path, *, capture: Callable = _capture_exact_public_pair,
    quote_get: Callable = public_quote,
    rules_get: Callable = public_rules,
    now_ms: Callable = lambda: time.time_ns() // 1_000_000,
) -> dict:
    """Every invocation is finite; all real inputs recorded when observed.

    Each public quote has its OWN local receipt timestamp recorded after
    returning from the network, never a candle-close spoofed timestamp.
    A pending decision can only be continued within <=60s from its original
    recorded timestamp, never replaced by a new historical decision.
    """
    account = status(path)
    symbol, interval = account["symbol"], account["interval"]
    old_pending = _pending(path)
    if old_pending:
        t = now_ms()
        if t <= old_pending[1] or t > old_pending[1] + MAX_QUOTE_DELAY_MS:
            raise PaperRefused(
                "PENDING_EXPIRED_OR_CLOCK_REVERSED_OPERATOR_REVIEW_REQUIRED"
            )
    venue_rules = rules_get(symbol)
    initial_quote = quote_get(symbol)
    first_mark = append_mark(
        path, initial_quote, venue_rules, received_local_ms=now_ms(),
    )
    if old_pending:
        decision = {"status": "RESUME_PREVIOUS_DURABLE_PENDING_QUOTE",
                    "signal_id": old_pending[0]}
    else:
        fresh = capture([symbol], limit=200, interval=interval)
        decision = observe(path, fresh, local_ms=now_ms())
        if decision["status"] == "IDEMPOTENT_SAME_BAR_NO_NEW_SIGNAL":
            return {
                "status": "MARKED_NO_NEW_CLOSED_BAR",
                "initial_mark": first_mark,
                "decision": decision, "report": report(path),
                "real_orders": 0,
            }
        if decision["status"] != "SIGNAL_RECORDED_AWAITING_POST_SIGNAL_QUOTE":
            raise PaperRefused("unrecognized research decision state")
    # The second quote is separately sampled AFTER actual signal receipt.
    trade_quote = quote_get(symbol)
    decision_fill = fill(
        path, trade_quote, venue_rules, quote_local_ms=now_ms(),
    )
    # The third quote marks ACTUAL paper holdings after the decision.
    # A failure here must not erase the already committed virtual fill;
    # restarting this controller will reconcile and resume safely.
    final_quote = quote_get(symbol)
    final_mark = append_mark(
        path, final_quote, venue_rules, received_local_ms=now_ms(),
    )
    return {
        "status": "POST_SIGNAL_PAPER_CYCLE_RECONCILED",
        "decision": decision, "fill": decision_fill,
        "pre_decision_mark": first_mark, "post_decision_mark": final_mark,
        "report": report(path),
        "real_orders": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--mode", choices=("once", "bounded-watch", "report"),
                        default="once")
    parser.add_argument("--poll-seconds", type=int, default=3600)
    parser.add_argument("--max-cycles", type=int, default=2)
    parser.add_argument("--acknowledge-foreground-only", action="store_true")
    parser.add_argument("--allow-public-market-data", action="store_true")
    parser.add_argument("--simulation-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.simulation_only:
        parser.error("must specify --simulation-only; REAL trades never permitted")
    if args.mode != "report" and not args.allow_public_market_data:
        parser.error("explicit public GET-only market-data consent required")
    if args.mode == "bounded-watch" and (
        not args.acknowledge_foreground_only
        or not 60 <= args.poll_seconds <= 3600
        or not 1 <= args.max_cycles <= 48
    ):
        parser.error("bounded-watch requires explicit foreground consent and 60..3600s/1..48 bounds")
    try:
        if args.mode == "report":
            output = report(args.ledger)
        elif args.mode == "once":
            output = cycle(args.ledger)
        else:
            outcomes = []
            for n in range(args.max_cycles):
                value = cycle(args.ledger)
                outcomes.append(value["status"])
                print(json.dumps({
                    "cycle": n + 1, "status": value["status"],
                    "report": value["report"],
                    "real_orders": 0,
                }, sort_keys=True), flush=True)
                if n + 1 < args.max_cycles:
                    time.sleep(args.poll_seconds)
            output = {
                "status": "BOUNDED_FOREGROUND_WATCH_COMPLETED",
                "cycles": len(outcomes), "outcomes": outcomes,
                "last_report": report(args.ledger),
            }
    except (ValueError, KeyError, TypeError, sqlite3.Error,
            OSError, AssertionError) as exc:
        print(json.dumps({
            "status": "RESEARCH_REFUSED_FAIL_CLOSED",
            "exception_class": type(exc).__name__,
            "reason": str(exc)[:220],
            "virtual_only": True,
            "real_orders": 0,
        }, sort_keys=True), flush=True)
        return 2
    print(json.dumps(output, sort_keys=True, allow_nan=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
