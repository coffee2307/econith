"""P2-05: prospective quote PnL, passive benchmark and safe controller tests."""
from __future__ import annotations

import json
import sqlite3

import pytest

from econith.quant.forward_quote_dryrun import (
    PaperRefused, initialize, observe, fill, status,
)
from econith.quant.paper_forward_monitor import append_mark, report
from scripts.paper_forward_cycle import cycle
from tests.test_forward_quote_dryrun import _book, _quote, _source


@pytest.fixture
def ledger(tmp_path):
    path = (tmp_path / "forward.sqlite").resolve()
    initialize(path)
    return path


class Clock:
    def __init__(self, ms):
        self.ms = ms
    def __call__(self):
        self.ms += 250
        return self.ms


def _fixture_source():
    snap, at = _source()
    return snap, at


def _cycle(ledger, *, clock=None, quotes=None, capture=None):
    snap, at = _fixture_source()
    q = _quote(snap)
    c = clock or Clock(at)
    quote_sequence = iter(quotes or [q, q, q])
    def get_quote(symbol):
        assert symbol == "BTCUSDT"
        return next(quote_sequence)
    def get_capture(symbols, *, limit, interval):
        assert symbols == ["BTCUSDT"] and limit == 200 and interval == "1h"
        return snap
    return cycle(ledger, capture=capture or get_capture,
                 quote_get=get_quote, rules_get=lambda s: _book(),
                 now_ms=c)


def test_first_forward_cycle_has_separate_quote_decision_then_fill_and_mark(ledger):
    x = _cycle(ledger)
    assert x["status"] == "POST_SIGNAL_PAPER_CYCLE_RECONCILED"
    assert x["decision"]["status"] == "SIGNAL_RECORDED_AWAITING_POST_SIGNAL_QUOTE"
    assert x["fill"]["status"] == "SIMULATED_FILL_AT_POST_SIGNAL_QUOTE"
    assert x["real_orders"] == 0
    assert x["pre_decision_mark"]["mark_sequence"] == 1
    assert x["post_decision_mark"]["mark_sequence"] == 2
    s = x["report"]
    assert s["performance"] == "OBSERVED_PUBLIC_QUOTE_SIMULATED_NOT_REAL_PROFIT"
    assert s["verified_quote_marks"] == 2
    assert s["strategy_total_return_pct"] < 0  # modeled ask/slippage/fees
    assert s["passive_20pct_equity_usdt"] > 0
    assert s["cash_control_return_pct"] == 0.
    assert s["prospective_profit_proven"] is False
    assert s["real_money_authorized"] is False


def test_idempotent_repeated_bar_only_creates_mark_not_duplicate_fill(ledger):
    snap, at = _fixture_source()
    _cycle(ledger, clock=Clock(at))
    result = _cycle(ledger, clock=Clock(at + 4000),
                    quotes=[_quote(snap)])
    assert result["status"] == "MARKED_NO_NEW_CLOSED_BAR"
    assert result["report"]["verified_quote_marks"] == 3
    assert status(ledger)["simulated_fills"] == 1
    assert status(ledger)["signal_count"] == 1
    assert status(ledger)["pending_signals"] == 0


def test_persisted_pending_can_be_resumed_before_expiry(ledger):
    snap, at = _fixture_source()
    ready = observe(ledger, snap, local_ms=at)
    assert ready["status"] == "SIGNAL_RECORDED_AWAITING_POST_SIGNAL_QUOTE"
    result = _cycle(ledger, clock=Clock(at + 2000))
    assert result["decision"]["status"] == "RESUME_PREVIOUS_DURABLE_PENDING_QUOTE"
    assert result["report"]["verified_quote_marks"] == 2
    assert status(ledger)["simulated_fills"] == 1


def test_expired_pending_requires_operator_review_no_quote_requests(ledger):
    snap, at = _fixture_source()
    observe(ledger, snap, local_ms=at)
    called = []
    def forbid(*args, **kw):
        called.append(1)
        raise AssertionError("unapproved after expiry")
    with pytest.raises(PaperRefused, match="PENDING_EXPIRED"):
        cycle(ledger, capture=forbid, quote_get=forbid,
              rules_get=forbid, now_ms=lambda: at+60002)
    assert called == []
    assert status(ledger)["simulated_fills"] == 0
    assert status(ledger)["pending_signals"] == 1


def test_forged_quote_mark_payload_cannot_be_read_as_profit(ledger):
    _cycle(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE forward_marks SET strategy_equity=99999 WHERE seq=2")
    with pytest.raises(PaperRefused, match="hash chain"):
        report(ledger)


def test_forged_quote_chain_order_cannot_be_read_as_profit(ledger):
    _cycle(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE forward_marks SET received_local_ms=1 WHERE seq=2")
    with pytest.raises(PaperRefused, match="chronology|hash chain"):
        report(ledger)


def test_bad_initial_spread_vetoes_before_any_decision(ledger):
    q = {"symbol": "BTCUSDT", "bidPrice": "90", "askPrice": "100"}
    with pytest.raises(PaperRefused, match="wide market"):
        _cycle(ledger, quotes=[q])
    assert status(ledger)["signal_count"] == 0
    assert report(ledger)["performance"] == "UNKNOWN_NO_FORWARD_MARKS"


def test_failed_post_decision_quote_keeps_persisted_pending_and_mark(ledger):
    snap, at = _fixture_source()
    clean = _quote(snap)
    bad = {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "120"}
    with pytest.raises(PaperRefused, match="wide market"):
        _cycle(ledger, quotes=[clean, bad], clock=Clock(at))
    assert status(ledger)["pending_signals"] == 1
    assert status(ledger)["simulated_fills"] == 0
    assert report(ledger)["verified_quote_marks"] == 1
    # Future invocation continues EXACT ORIGINAL signal without replacement.
    result = _cycle(ledger, clock=Clock(at+1000))
    assert result["decision"]["status"] == "RESUME_PREVIOUS_DURABLE_PENDING_QUOTE"
    assert status(ledger)["signal_count"] == 1
    assert status(ledger)["simulated_fills"] == 1


def test_invalid_cash_reserve_does_not_make_fake_passive_trade(ledger):
    snap, at = _fixture_source()
    q = {"symbol":"BTCUSDT", "bidPrice":"10000", "askPrice":"10000.01"}
    with pytest.raises(PaperRefused, match="deviates"):
        append_mark(ledger, q, _book(), received_local_ms=at)
    assert report(ledger)["verified_quote_marks"] == 0


def test_quote_benchmark_is_cash_conserving_and_does_not_retrade(ledger):
    snap, at = _fixture_source()
    first = append_mark(ledger, _quote(snap), _book(), received_local_ms=at)
    assert first["strategy_return_pct"] == 0
    second = append_mark(ledger, _quote(snap), _book(),
                         received_local_ms=at+1000)
    assert second["mark_sequence"] == 2
    r = report(ledger)
    assert r["strategy_total_return_pct"] == 0.
    assert r["passive_20pct_total_return_pct"] < 0.  # spread/fees
    assert r["excess_strategy_vs_passive_usdt"] > 0
    with sqlite3.connect(ledger) as db:
        d = db.execute("SELECT passive_cash, passive_qty FROM forward_marks").fetchall()
    assert len(d) == 2 and d[0] == d[1]


def test_old_single_fill_ledger_cannot_claim_benchmark_from_genesis(ledger):
    snap, at = _fixture_source()
    observe(ledger, snap, local_ms=at)
    fill(ledger, _quote(snap), _book(), quote_local_ms=at+250)
    first = append_mark(ledger, _quote(snap), _book(),
                        received_local_ms=at+500)
    r = report(ledger)
    assert first["status"] == "PUBLIC_QUOTE_PAPER_MARK_RECORDED"
    assert r["starting_with_preexisting_virtual_position"] is True
    assert r["benchmark_starts_at_first_independently_observed_quote"] is True


def test_report_detects_trade_after_latest_mark_and_refuses_profit_claim(ledger):
    snap, at = _fixture_source()
    append_mark(ledger, _quote(snap), _book(), received_local_ms=at)
    observe(ledger, snap, local_ms=at+100)
    fill(ledger, _quote(snap), _book(), quote_local_ms=at+200)
    assert report(ledger)["performance"] == "STALE_MARK_REQUIRES_FRESH_QUOTE"
    assert report(ledger)["prospective_profit_proven"] is False


def test_original_existing_dryrun_account_no_marks_report_unknown(ledger):
    r = report(ledger)
    assert r["verified_quote_marks"] == 0
    assert r["performance"] == "UNKNOWN_NO_FORWARD_MARKS"
    assert r["real_money_authorized"] is False
