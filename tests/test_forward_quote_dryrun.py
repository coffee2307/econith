"""A deliberately narrow Freqtrade-like dry-run capability for ECONITH.

Validates real research invariants: no orders, no backdated fills, no stolen
pending decision after restart, cash conservation and independent risk veto.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from econith.quant.forward_quote_dryrun import (
    PaperRefused, initialize, observe, fill, status, public_quote, public_rules,
)
from tests.test_paper_unfiltered_matrix import fake_snapshot


def _source(n=200, trend="up", symbol="BTCUSDT"):
    x = fake_snapshot(symbol, "1h", n=n, trend=trend)
    return x, int(x["market"][symbol][-1][6]) + 1000


def _quote(snap, spread_bps=2):
    close = float(snap["market"]["BTCUSDT"][-1][4])
    return {"symbol": "BTCUSDT",
            "bidPrice": str(close * (1 - spread_bps / 20000)),
            "askPrice": str(close * (1 + spread_bps / 20000))}


def _book():
    return {"step_size": "0.001", "min_notional": 5.}


@pytest.fixture
def ledger(tmp_path):
    root = (tmp_path / "forward_paper.sqlite").resolve()
    assert initialize(root)["real_orders"] == 0
    return root


def _prepare(root, n=200, trend="up"):
    s, now = _source(n, trend)
    decision = observe(root, s, local_ms=now)
    assert decision["status"] == "SIGNAL_RECORDED_AWAITING_POST_SIGNAL_QUOTE"
    assert decision["real_orders"] == 0
    return s, now, decision


def test_private_init_sets_no_live_keys_and_restart_safe_state(ledger):
    initial = status(ledger)
    assert initial["capital_readiness"] == "REAL_CAPITAL_NOT_AUTHORIZED"
    assert initial["virtual_cash_usdt"] == 100.
    assert initial["virtual_asset_quantity"] == 0.
    assert initial["real_orders"] == 0
    assert initial["simulated_fills"] == 0
    with pytest.raises(PaperRefused, match="already exists"):
        initialize(ledger)


def test_record_future_signal_then_later_quote_fill_and_restart_no_duplicate(ledger):
    s, now, decision = _prepare(ledger)
    assert decision["desired_weight"] > 0
    quote = _quote(s)
    result = fill(ledger, quote, _book(), quote_local_ms=now + 500)
    assert result["status"] == "SIMULATED_FILL_AT_POST_SIGNAL_QUOTE"
    assert result["real_orders"] == 0
    state = status(ledger)
    assert state["virtual_cash_usdt"] < 100
    assert state["virtual_asset_quantity"] > 0
    assert state["simulated_fills"] == 1
    assert state["pending_signals"] == 0
    # Re-open the same SQLite from a separate state object/process.
    with pytest.raises(PaperRefused, match="no unresolved"):
        fill(ledger, quote, _book(), quote_local_ms=now + 800)
    assert status(ledger)["simulated_fills"] == 1


def test_observe_same_bar_idempotent_no_duplicate_decision(ledger):
    s, now, _ = _prepare(ledger)
    seen = observe(ledger, s, local_ms=now + 50)
    assert seen["status"] == "IDEMPOTENT_SAME_BAR_NO_NEW_SIGNAL"
    assert status(ledger)["signal_count"] == 1
    assert status(ledger)["pending_signals"] == 1


def test_no_backdated_quote_or_same_timestamp_synthetic_fill(ledger):
    s, now, _ = _prepare(ledger)
    for t in (now-1, now, now+60001):
        with pytest.raises(PaperRefused, match="quote stale"):
            fill(ledger, _quote(s), _book(), quote_local_ms=t)
    assert status(ledger)["pending_signals"] == 1
    assert status(ledger)["simulated_fills"] == 0


@pytest.mark.parametrize("fake_quote", [
    {"symbol": "DOGEUSDT", "bidPrice": "100", "askPrice": "101"},
    {"symbol": "BTCUSDT", "bidPrice": "101", "askPrice": "99"},
    {"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "110"},
    {"symbol": "BTCUSDT", "bidPrice": "NaN", "askPrice": "110"},
])
def test_risk_council_vetoes_wrong_source_crossed_or_wide_book(ledger, fake_quote):
    _, now, _ = _prepare(ledger)
    with pytest.raises(PaperRefused):
        fill(ledger, fake_quote, _book(), quote_local_ms=now+100)
    assert status(ledger)["simulated_fills"] == 0


def test_volatile_price_shock_veto_not_override(ledger):
    _, now, _ = _prepare(ledger)
    with pytest.raises(PaperRefused, match="jumped"):
        fill(ledger, {"symbol": "BTCUSDT", "bidPrice": "1000",
                     "askPrice": "1000.01"}, _book(), quote_local_ms=now+100)
    assert status(ledger)["pending_signals"] == 1


def test_no_future_bar_can_be_relabelled_to_hide_missing_time(ledger):
    s, now, _ = _prepare(ledger)
    fill(ledger, _quote(s), _book(), quote_local_ms=now+500)
    far, far_now = _source(202)
    with pytest.raises(PaperRefused, match="gap or skipped"):
        observe(ledger, far, local_ms=far_now)
    assert status(ledger)["signal_count"] == 1


def test_changed_prior_source_bar_fails_closed_even_if_newest_bar_is_valid(ledger):
    s, now, _ = _prepare(ledger)
    fill(ledger, _quote(s), _book(), quote_local_ms=now+500)
    newer, newer_now = _source(201)
    # Poison a previously persisted bar but leave shape/time unchanged.
    row = newer["market"]["BTCUSDT"][65]
    row[1] = str(float(row[1]) * 1.001)
    with pytest.raises(PaperRefused, match="revised"):
        observe(ledger, newer, local_ms=newer_now)
    assert status(ledger)["signal_count"] == 1


def test_one_new_bar_fails_if_previous_order_is_pending(ledger):
    _prepare(ledger)
    newer, now = _source(201)
    with pytest.raises(PaperRefused, match="pending"):
        observe(ledger, newer, local_ms=now)
    assert status(ledger)["signal_count"] == 1


def test_next_bar_after_sim_fill_creates_single_new_signal(ledger):
    old, now, _ = _prepare(ledger)
    fill(ledger, _quote(old), _book(), quote_local_ms=now+500)
    newer, next_ms = _source(201)
    result = observe(ledger, newer, local_ms=next_ms)
    assert result["status"] == "SIGNAL_RECORDED_AWAITING_POST_SIGNAL_QUOTE"
    assert result["signal_id"] == 2
    assert status(ledger)["signal_count"] == 2


def test_flat_price_no_virtual_trade_but_decision_consumed(ledger):
    s, now, decision = _prepare(ledger, trend="flat")
    assert decision["desired_weight"] == 0.
    result = fill(ledger, _quote(s), _book(), quote_local_ms=now+10)
    assert result["status"] == "NO_FILL_SIZE_FILTER"
    assert status(ledger)["pending_signals"] == 0
    assert status(ledger)["simulated_fills"] == 0
    assert status(ledger)["virtual_cash_usdt"] == 100.


def test_source_gap_in_first_snapshot_is_not_accepted(ledger):
    s, now = _source()
    s["market"]["BTCUSDT"].pop(80)
    with pytest.raises(ValueError, match="missing"):
        observe(ledger, s, local_ms=now)
    assert status(ledger)["signal_count"] == 0


def test_tampering_journal_history_hash_quarantines_future_steps(ledger):
    _prepare(ledger)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE account SET history_sha256=? WHERE id=1", ("0"*64,))
    with pytest.raises(PaperRefused, match="integrity"):
        status(ledger)


def test_quote_adapter_is_pinned_public_data_only(monkeypatch):
    import econith.quant.forward_quote_dryrun as unit
    called = []
    def fake_get(endpoint, params):
        called.append((endpoint, params))
        if endpoint.endswith("bookTicker"):
            return json.dumps({"symbol": "BTCUSDT",
                               "bidPrice": "100", "askPrice": "100.1"}).encode()
        raise AssertionError("unapproved network path")
    monkeypatch.setattr(unit, "request_public", fake_get)
    assert public_quote("BTCUSDT")["symbol"] == "BTCUSDT"
    assert called == [("/api/v3/ticker/bookTicker", {"symbol": "BTCUSDT"})]


def test_invalid_state_and_unknown_strategy_refuse(tmp_path):
    root = (tmp_path / "bad.sqlite").resolve()
    with pytest.raises(PaperRefused):
        initialize(root, symbol="DOGUSDT", strategy="LLM_AUTO_TRADE", capital=100)
    assert not root.exists()


@pytest.mark.parametrize("field,corrupt", [
    ("cash", 100.),
    ("qty", .99),
])
def test_forged_virtual_holdings_rejected_on_restart(ledger, field, corrupt):
    snap, now, _ = _prepare(ledger)
    fill(ledger, _quote(snap), _book(), quote_local_ms=now+100)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE account SET " + field + "=? WHERE id=1", (corrupt,))
    with pytest.raises(PaperRefused, match="diverged"):
        status(ledger)


def test_forged_fee_or_fill_economics_quarantined(ledger):
    snap, now, _ = _prepare(ledger)
    fill(ledger, _quote(snap), _book(), quote_local_ms=now+100)
    with sqlite3.connect(ledger) as db:
        db.execute("UPDATE paper_fills SET fee=fee+1 WHERE decision_id=1")
    with pytest.raises(PaperRefused, match="economics"):
        status(ledger)
