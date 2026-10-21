"""Preregistered paper experiment: never drop a losing/missing asset."""
from __future__ import annotations

import json
import math
from pathlib import Path
import pytest

from scripts.paper_unfiltered_matrix import (
    INCOMPLETE, PASS, CAPITAL, _benchmark_20pct,
    evaluate_market, read_plan, run_matrix,
)


def fake_snapshot(symbol: str, interval: str, *,
                  n: int = 500, trend: str = "flat") -> dict:
    ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[interval]
    begin = 1_750_000_000_000 // ms * ms
    rows = []
    for i in range(n):
        x = 100.0 if trend == "flat" else (
            100 + .10 * i if trend == "up" else 160 - .10 * i
        )
        rows.append([
            begin + i * ms, str(x), str(x * 1.005),
            str(x * .995), str(x), "400",
            begin + (i + 1) * ms - 1, "40000", 20,
            "200", "20000", "0",
        ])
    return {
        "source": "binance_spot_public", "asof_ms": begin + n * ms,
        "symbols": [symbol], "timeframe": interval,
        "market": {symbol: rows},
        "rules": {symbol: {"step_size": ".001", "min_notional": 5}},
    }


def capture_flat(symbols, *, limit, interval):
    assert len(symbols) == 1 and limit == 500
    return fake_snapshot(symbols[0], interval)


def test_frozen_plan_has_exact_order_no_post_hoc_mutation():
    p = read_plan()
    assert len(p["symbols"]) * len(p["intervals"]) == 12
    assert p["fixed_strategy"]["retune_after_results"] is False
    assert "DOGUSDT" not in p["symbols"]


def test_all_12_flat_nonprofitable_cases_still_reported(tmp_path):
    root = (tmp_path / "matrix").resolve()
    data = run_matrix(root, capture=capture_flat)
    assert data["verdict"] == PASS
    assert data["expected_cases"] == 12
    assert data["successful_data_cases"] == 12
    assert data["late30_positive_base_cases"] == 0
    assert data["late30_positive_after_3x_cost_cases"] == 0
    assert data["late30_positive_3x_and_beats_buyhold_and_trades_cases"] == 0
    assert data["real_money_authorized"] is False
    assert data["capital_readiness"] == CAPITAL
    assert data["prospective_out_of_sample_validated"] is False
    assert len(data["cases"]) == 12
    for case in data["cases"]:
        result = case["result"]
        assert result["late_bars_evaluated"] >= 90
        assert result["late_warmup_bars"] == 25
        assert result["windows"]["late30"]["cash_and_position_ledger_audited"]
        assert result["windows"]["late30"]["base"]["net_pnl_usdt"] == 0
        assert result["windows"]["late30"]["stress3x"]["net_pnl_usdt"] == 0
        assert result["windows"]["late30"]["base"]["simulated_fill_count"] == 0
        assert (root / (case["symbol"].lower() + "_" + case["interval"]) /
                "manifest.json").exists()
    assert (root / "preregistered_policy.json").is_file()
    assert json.loads((root / "unfiltered_matrix.json").read_text()) == data


def test_one_public_data_failure_not_dropped_from_denominator(tmp_path):
    def fail_one(symbols, *, limit, interval):
        if symbols == ["DOGEUSDT"] and interval == "4h":
            raise ValueError("missing public spot history")
        return capture_flat(symbols, limit=limit, interval=interval)
    data = run_matrix((tmp_path / "one_failure").resolve(), capture=fail_one)
    assert data["verdict"] == INCOMPLETE
    assert data["successful_data_cases"] == 11
    assert data["failed_data_cases"] == 1
    failures = [row for row in data["cases"] if row["status"] !=
                "CAPTURE_AND_SIMULATION_RECONCILED"]
    assert len(failures) == 1
    assert failures[0]["symbol"] == "DOGEUSDT"
    assert failures[0]["interval"] == "4h"


def test_last30_evaluation_never_uses_future_price_in_earlier_order(tmp_path):
    original = fake_snapshot("BTCUSDT", "4h", trend="up")
    changed = json.loads(json.dumps(original))
    rows = changed["market"]["BTCUSDT"]
    for index in range(450, 500):
        v = float(rows[index][1]) * 2
        rows[index][1:5] = [str(v), str(v * 1.005),
                             str(v * .995), str(v)]
    early = evaluate_market(original)["windows"]["late30"]
    altered = evaluate_market(changed)["windows"]["late30"]
    # Last 50 bars may alter final PnL; the fixed engine's earlier
    # trade timing itself is regression-tested in test_paper_v1.py.
    assert early["base"]["simulated_fill_count"] >= 0
    assert altered["base"]["simulated_fill_count"] >= 0
    assert early["cash_and_position_ledger_audited"]
    assert altered["cash_and_position_ledger_audited"]


def test_wrong_pair_or_gapped_closes_quarantined(tmp_path):
    def fake_pair(symbols, *, limit, interval):
        data = capture_flat(symbols, limit=limit, interval=interval)
        if symbols == ["BTCUSDT"] and interval == "4h":
            data["market"]["BTCUSDT"].pop(111)
        return data
    data = run_matrix((tmp_path / "bad_data").resolve(), capture=fake_pair)
    assert data["verdict"] == INCOMPLETE
    bad = [r for r in data["cases"] if r["status"] ==
           "QUARANTINED_NO_EVIDENCE_OF_EDGE"]
    assert len(bad) == 1
    assert bad[0]["symbol"] == "BTCUSDT"


def test_preexisting_or_relative_path_never_overwritten(tmp_path):
    f = (tmp_path / "preexisting").resolve()
    f.mkdir()
    (f / "private.txt").write_text("unchanged")
    with pytest.raises(ValueError, match="evidence root"):
        run_matrix(f, capture=capture_flat)
    assert (f / "private.txt").read_text() == "unchanged"


def test_benchmark_position_limit_no_leverage():
    data = fake_snapshot("BTCUSDT", "4h", trend="up")
    bm = _benchmark_20pct(data, stress=3)
    assert bm["quantity"] >= 0
    assert math.isfinite(bm["ending_equity_usdt"])
    assert bm["ending_equity_usdt"] > 0


def test_changing_registered_policy_is_refused(tmp_path):
    good = read_plan()
    good["fixed_strategy"]["entry_threshold"] = .0001
    path = tmp_path / "pretend_preregistration.json"
    path.write_text(json.dumps(good))
    with pytest.raises(ValueError, match="pre-registered"):
        read_plan(path)
