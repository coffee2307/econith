"""P2-03: deterministic, no-lookahead paper challenger acceptance.

A negative return is NOT a unit test failure and must never be removed.
"""
from __future__ import annotations

import copy
import json
import math

import pytest

from econith.quant.classical_challengers import NAMES, CANDIDATE_WARMUP, policy, target
from econith.quant.paper_v1 import PaperConfig, replay_spot, validated_candles, SymbolRules
from scripts.quant_classical_challenge import (
    PASS, INCOMPLETE, evaluate, read_frozen_plan, run_all, _report_market,
)
from tests.test_paper_unfiltered_matrix import fake_snapshot


def test_exact_strategy_registry_and_parameter_lock():
    cfg, digest = read_frozen_plan()
    assert len(digest) == 64
    assert cfg["compare"] == list(NAMES)
    assert cfg["lookback_warmup"] == 97
    assert [v["multiplier"] for v in cfg["frictions"]] == [1, 3, 5]
    assert cfg["design_locked_before_first_public_benchmark_run"] is True


def test_future_bars_dont_affect_earlier_trading_signal():
    snap = fake_snapshot("BTCUSDT", "4h", trend="up")
    bars = validated_candles(snap["market"]["BTCUSDT"],
                             timeframe_ms=14_400_000, asof_ms=snap["asof_ms"])
    prefix = tuple(bars[:250])
    for name in NAMES:
        before = target(name, prefix)
        future = tuple(bars[:250]) + tuple(bars[250:])
        assert target(name, future[:250]) == before
        assert 0 <= before <= .2
    with pytest.raises(ValueError, match="immutable"):
        target("trend_vol_v1", list(prefix))


def test_engine_policy_can_only_see_preceding_close_not_fill_bar():
    snap = fake_snapshot("BTCUSDT", "4h", trend="up", n=200)
    bars = validated_candles(snap["market"]["BTCUSDT"],
                             timeframe_ms=14_400_000, asof_ms=snap["asof_ms"])
    evidence = []
    def spy(name, history):
        assert isinstance(history, tuple)
        assert history[-1].close_ms < bars[len(history)].open_ms
        assert tuple(bars[:len(history)]) == history
        evidence.append(len(history))
        return 0.0
    report = replay_spot(
        {"BTCUSDT": bars},
        {"BTCUSDT": SymbolRules(step_size=".001", min_notional=5)},
        PaperConfig(initial_cash=100, timeframe_ms=14_400_000, lookback=97),
        signal_policy=spy,
    )
    assert evidence[0] == 98 and evidence[-1] == 199
    assert report.final_equity == 100.
    assert report.fills == []


@pytest.mark.parametrize("bad", [-.1, .21, float("nan"), float("inf"), None, "BUY"])
def test_unbounded_policy_output_fail_closed(bad):
    snap = fake_snapshot("BTCUSDT", "4h", n=200)
    bars = validated_candles(snap["market"]["BTCUSDT"],
                             timeframe_ms=14_400_000, asof_ms=snap["asof_ms"])
    with pytest.raises(ValueError, match="target"):
        replay_spot(
            {"BTCUSDT": bars},
            {"BTCUSDT": SymbolRules(step_size=".001", min_notional=5)},
            PaperConfig(initial_cash=100, timeframe_ms=14_400_000, lookback=97),
            signal_policy=lambda *args: bad,
        )


@pytest.mark.parametrize("family", NAMES)
def test_all_families_no_trade_flat_market_and_audited_cash(family):
    snap = fake_snapshot("BTCUSDT", "4h")
    frozen, _ = read_frozen_plan()
    r = _report_market(snap, family, frozen["frictions"][2])
    assert r["net_pnl_usdt"] == 0.
    assert r["simulated_fills"] == 0
    assert r["ledger_status"] == "SIMULATED_LEDGER_RECONCILED"
    assert r["orders_submitted"] == 0


def test_corrupt_future_bar_is_refused_no_fill_audit():
    snap = fake_snapshot("BTCUSDT", "4h")
    snap["market"]["BTCUSDT"].pop(300)
    with pytest.raises(ValueError, match="missing"):
        evaluate(snap)


def test_every_family_is_reported_in_both_windows_and_3_cost_levels():
    snap = fake_snapshot("SOLUSDT", "1d", trend="up")
    out = evaluate(snap)
    assert out["window_latest30_evaluation_candles"] >= 149
    assert out["signal_warmup_candles"] == 98
    for win in ("full", "late30"):
        assert set(out["windows"][win]) == set(NAMES)
        for fam in NAMES:
            x = out["windows"][win][fam]
            assert set(x) == {"base", "stress3x", "stress5x"}
            assert all(k["ledger_status"] == "SIMULATED_LEDGER_RECONCILED"
                       for k in x.values())
            assert all(k["orders_submitted"] == 0 for k in x.values())


def test_all_12_negative_zero_market_cases_are_never_removed(tmp_path):
    def capture(symbols, *, limit, interval):
        return fake_snapshot(symbols[0], interval)
    root = (tmp_path / "twelve").resolve()
    report = run_all(root, capture=capture)
    assert report["verdict"] == PASS
    assert report["expected_strategy_market_timeframe_cases"] == 60
    assert report["completed_market_timeframe_cases"] == 12
    assert len(report["results"]) == 12
    assert report["real_exchange_orders"] == 0
    for case in report["results"]:
        assert case["status"] == "COMPLETE"
        for win in ("full", "late30"):
            for fam in NAMES:
                for expense in ("base", "stress3x", "stress5x"):
                    item = case["result"]["windows"][win][fam][expense]
                    assert item["net_pnl_usdt"] == 0.
                    assert item["simulated_fills"] == 0
    assert json.loads((root / "unfiltered_challengers.json").read_text()) == report


def test_missing_one_pair_quarantined_in_full_denominator(tmp_path):
    def fail_one(symbols, *, limit, interval):
        if symbols == ["DOGEUSDT"] and interval == "1d":
            raise ValueError("public Spot pair not available")
        return fake_snapshot(symbols[0], interval)
    report = run_all((tmp_path / "one_failed").resolve(), capture=fail_one)
    assert report["verdict"] == INCOMPLETE
    assert report["completed_market_timeframe_cases"] == 11
    assert len(report["results"]) == 12
    assert len([x for x in report["results"] if x["status"] != "COMPLETE"]) == 1
    assert report["capital_readiness"] == "REAL_CAPITAL_NOT_AUTHORIZED"


def test_existing_output_directory_is_not_ever_overwritten(tmp_path):
    root = (tmp_path / "exist").resolve()
    root.mkdir()
    (root / "do_not_delete.txt").write_text("PRIVATE")
    with pytest.raises(ValueError):
        run_all(root, capture=lambda *a, **k: None)
    assert (root / "do_not_delete.txt").read_text() == "PRIVATE"


def test_legacy_default_engine_still_matches_explicit_frozen_momentum():
    snap = fake_snapshot("BTCUSDT", "4h", trend="up", n=200)
    bars = validated_candles(snap["market"]["BTCUSDT"],
                             timeframe_ms=14_400_000, asof_ms=snap["asof_ms"])
    rules = {"BTCUSDT": SymbolRules(step_size=".001", min_notional=5)}
    cfg = PaperConfig(
        initial_cash=100, timeframe_ms=14_400_000,
        lookback=24, entry_threshold=.01, max_asset_weight=.2,
        max_total_weight=.2, max_drawdown=.15,
    )
    standard = replay_spot({"BTCUSDT": bars}, rules, cfg)
    with_callback = replay_spot({"BTCUSDT": bars}, rules, cfg,
                                signal_policy=policy("frozen_momentum24"))
    assert standard.to_dict() == with_callback.to_dict()
    assert all(x["execution_mode"] == "SIMULATION" for x in with_callback.to_dict()["fills"])
