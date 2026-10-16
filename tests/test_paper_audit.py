"""Truthful portfolio accounting and capital-sizing tests for offline paper mode."""
from __future__ import annotations

import copy

import pytest

from econith.quant.paper_audit import PaperAuditError, audit_paper_report
from econith.quant.paper_v1 import Candle, PaperConfig, SymbolRules, replay_spot


def _bars(n: int = 70) -> list[Candle]:
    base = 1_700_000_000_000
    output = []
    for i in range(n):
        price = 100 + 2 * i if i < 35 else 100 + 2 * (70 - i)
        output.append(
            Candle(base + i * 3_600_000, base + (i + 1) * 3_600_000 - 1,
                   price, price + 1, price - 1, price, 1000)
        )
    return output


def _report(capital: float = 1000) -> dict:
    cfg = PaperConfig(initial_cash=capital, lookback=3, entry_threshold=.003,
                      max_asset_weight=.20, max_total_weight=.60,
                      max_drawdown=.50)
    return replay_spot(
        {"BTCUSDT": _bars()},
        {"BTCUSDT": SymbolRules("0.00001", 5.0)}, cfg
    ).to_dict()


def test_realized_and_unrealized_reconcile_total_equity() -> None:
    original = _report()
    audit = audit_paper_report(original)
    assert audit["status"] == "SIMULATED_LEDGER_RECONCILED"
    assert audit["closed_sell_legs"] > 0
    assert audit["explicit_fees_paid"] > 0
    assert (audit["realized_net_pnl"] + audit["unrealized_net_pnl"]
            == pytest.approx(audit["total_net_pnl"], abs=1e-5))
    assert audit["evolution_reward_eligible"] is False


@pytest.mark.parametrize("capital", [10, 100, 1000])
def test_small_demo_capital_requires_valid_min_notional(capital: int) -> None:
    report = _report(capital)
    summary = audit_paper_report(report)
    assert summary["initial_cash"] == capital
    if capital == 10:
        assert len(report["fills"]) == 0
        assert summary["total_net_pnl"] == 0
    else:
        assert len(report["fills"]) > 0
    assert summary["evolution_reward_eligible"] is False


def test_detects_forged_cash_balance() -> None:
    forged = copy.deepcopy(_report())
    forged["equity_curve"][20]["cash"] += 1
    with pytest.raises(PaperAuditError, match="reconcile"):
        audit_paper_report(forged)


def test_detects_fake_reported_profit() -> None:
    forged = copy.deepcopy(_report())
    forged["final_equity"] += 100
    with pytest.raises(PaperAuditError, match="final equity"):
        audit_paper_report(forged)


def test_detects_non_simulated_fill() -> None:
    forged = copy.deepcopy(_report())
    forged["fills"][0]["execution_mode"] = "REALITY"
    with pytest.raises(PaperAuditError, match="non-simulation"):
        audit_paper_report(forged)


def test_detects_duplicate_or_oversold_fill() -> None:
    forged = copy.deepcopy(_report())
    sale = next(f for f in forged["fills"] if f["side"] == "SELL")
    sale["quantity"] = sale["quantity"] * 2
    with pytest.raises(PaperAuditError, match="short sale|reconcile"):
        audit_paper_report(forged)


def test_detects_tampered_fee() -> None:
    forged = copy.deepcopy(_report())
    forged["fills"][0]["fee"] += 1
    with pytest.raises(PaperAuditError, match="reconcile"):
        audit_paper_report(forged)
