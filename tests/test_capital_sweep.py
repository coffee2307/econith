"""Offline, reproducible $10–$1000 capital stress without real orders."""
from __future__ import annotations

from scripts.paper_capital_sweep import sweep_capitals


def _fixture() -> dict:
    base = 1_700_000_000_000
    n = 70
    rows = []
    for i in range(n):
        px = 100 + 2 * i if i < 35 else 100 + 2 * (70-i)
        rows.append([
            base + 14_400_000 * i, str(px), str(px+1), str(px-1),
            str(px), "100", base + 14_400_000 * (i+1)-1,
        ])
    return {
        "asof_ms": base + n*14_400_000,
        "timeframe": "4h",
        "market": {"BTCUSDT": rows},
        "rules": {"BTCUSDT": {"step_size": "0.00001", "min_notional": 5.0}},
    }


def test_capital_sweep_reconciles_every_scenario() -> None:
    result = sweep_capitals(_fixture())
    assert result["capital_usdt"] == [10, 100, 1000]
    assert result["bar_count"] == 70
    assert result["real_exchange_orders"] == 0
    assert result["high_confidence_profitable"] is False
    assert result["readiness"] == "NOT_READY_FOR_CAPITAL_OR_AUTONOMOUS_DEMO"
    assert len(result["scenarios"]) == 6
    assert all(x["reconciliation_status"] == "SIMULATED_LEDGER_RECONCILED"
               for x in result["scenarios"])
    assert all(x["reproduction_eligible"] is False for x in result["scenarios"])


def test_ten_usdt_cannot_force_minimum_order() -> None:
    s = sweep_capitals(_fixture())["scenarios"]
    ten = [x for x in s if x["capital"] == 10]
    assert len(ten) == 2
    assert all(x["no_valid_orders"] and x["fill_count"] == 0 for x in ten)
    assert all(x["return_pct"] == 0 for x in ten)
    assert any(x["fill_count"] > 0 for x in s if x["capital"] == 100)
    assert any(x["fill_count"] > 0 for x in s if x["capital"] == 1000)
