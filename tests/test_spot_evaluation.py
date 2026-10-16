"""Pure in-memory regression checks for comparative spot research.

No API keys, no exchange calls, no disk writes, no real funds.
"""
from __future__ import annotations

import pytest

import scripts.evaluate_spot_replay as evaluation


def _snapshot(bars: int = 100) -> dict:
    base = 1_700_000_000_000
    rows = [
        [
            base + i * 3_600_000,
            str(100 + i * .25),
            str(101 + i * .25),
            str(99 + i * .25),
            str(100.2 + i * .25),
            "100",
            base + (i + 1) * 3_600_000 - 1,
        ]
        for i in range(bars)
    ]
    return {
        "timeframe": "1h",
        "asof_ms": base + bars * 3_600_000,
        "market": {"BTCUSDT": rows},
        "rules": {"BTCUSDT": {"step_size": "0.00001", "min_notional": 5.0}},
    }


def test_comparative_evaluation_refuses_profitability_readiness(monkeypatch) -> None:
    monkeypatch.setattr(evaluation, "load_snapshot", lambda path: _snapshot())
    output = evaluation.evaluate(None)
    assert output["readiness"] == "NOT_READY_FOR_PROFITABILITY_OR_LIVE_EXECUTION"
    assert set(output["scenarios"]) == {"full", "first_70pct", "last_30pct"}
    full = output["scenarios"]["full"]["rules"]
    assert set(full) == {
        "friction_free", "base_cost", "triple_cost", "passive_buy_hold", "cash"
    }
    assert full["cash"]["net_return_pct"] == 0.0
    assert full["base_cost"]["fill_count"] > 0
    # On a single-trade monotonic sample, extra friction must reduce return.
    assert (
        full["friction_free"]["net_return_pct"] >
        full["base_cost"]["net_return_pct"] >
        full["triple_cost"]["net_return_pct"]
    )


def test_insufficient_candle_history_refused(monkeypatch) -> None:
    monkeypatch.setattr(evaluation, "load_snapshot", lambda path: _snapshot(40))
    with pytest.raises(ValueError, match="insufficient"):
        evaluation.evaluate(None)


def test_report_contains_realistic_limits(monkeypatch) -> None:
    monkeypatch.setattr(evaluation, "load_snapshot", lambda path: _snapshot())
    output = evaluation.evaluate(None)
    assert output["methodology"]["fee_bps_one_side"] == 10.0
    assert output["methodology"]["sample_split"].startswith("independently restarted")
    assert output["scenarios"]["full"]["rules"]["base_cost"]["bars"] == 100
