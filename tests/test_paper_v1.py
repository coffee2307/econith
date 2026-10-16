"""Regression tests for the isolated, no-network ECONITH paper replay."""
from __future__ import annotations

import pytest

from econith.quant.paper_v1 import (
    Candle, PaperConfig, SymbolRules, replay_spot, validated_candles,
)


def series(start=100.0, step=2.0, n=30, base=1700000000000):
    out = []
    for i in range(n):
        price = start + step * i if i < 18 else start + step * (36-i)
        price = max(1.0, price)
        out.append(Candle(base + 3600000 * i, base + 3600000 * (i + 1) - 1,
                          price, price * 1.01, price * 0.99, price, 100.0))
    return out


def cfg(**kw):
    defaults = dict(initial_cash=10000.0, lookback=3, entry_threshold=0.002,
                    max_asset_weight=0.2, max_total_weight=0.6, fee_bps=10,
                    slippage_bps=2, half_spread_bps=1, max_drawdown=0.8)
    return PaperConfig(**{**defaults, **kw})


def test_buys_and_sells_use_next_open_and_pay_fees():
    candles = series()
    report = replay_spot({"BTCUSDT": candles}, {"BTCUSDT": SymbolRules("0.001", 5)}, cfg())
    assert {x.side for x in report.fills} == {"BUY", "SELL"}
    assert all(x.execution_mode == "SIMULATION" for x in report.fills)
    assert all(x.fee > 0 for x in report.fills)
    for fill in report.fills:
        c = next(b for b in candles if b.open_ms == fill.timestamp_ms)
        assert (fill.price > c.open if fill.side == "BUY" else fill.price < c.open)
    assert report.final_equity == pytest.approx(report.equity_curve[-1]["equity"])


def test_two_symbols_use_one_portfolio_and_never_borrow():
    a, b = series(), series(start=200, step=4)
    report = replay_spot({"ETHUSDT": b, "BTCUSDT": a},
                         {"BTCUSDT": SymbolRules("0.001", 5),
                          "ETHUSDT": SymbolRules("0.001", 5)}, cfg())
    assert len(report.equity_curve) == len(a)
    assert all(row["cash"] >= -1e-7 for row in report.equity_curve)
    assert all(row["exposure"] / row["equity"] <= 0.62 for row in report.equity_curve)
    assert all(q >= 0 for q in report.last_positions.values())


def test_future_price_changes_do_not_change_earlier_fills():
    a = series()
    b = list(a)
    c = b[-1]
    b[-1] = Candle(c.open_ms, c.close_ms, 500.0, 501.0, 499.0, 500.0, 100.0)
    rules = {"BTCUSDT": SymbolRules("0.001", 5)}
    x = replay_spot({"BTCUSDT": a}, rules, cfg()).fills
    y = replay_spot({"BTCUSDT": b}, rules, cfg()).fills
    assert [f for f in x if f.timestamp_ms < c.open_ms] == [f for f in y if f.timestamp_ms < c.open_ms]


def test_unclosed_candle_rejected():
    bar = series(n=1)[0]
    with pytest.raises(ValueError, match="unclosed"):
        validated_candles([
            {k: getattr(bar,k) for k in ("open_ms", "close_ms", "open", "high", "low", "close", "volume")}
        ], timeframe_ms=3600000, asof_ms=bar.close_ms)


def test_gap_rejected():
    c = series(n=4)
    with pytest.raises(ValueError, match="missing"):
        validated_candles([c[0], c[2]], timeframe_ms=3600000, asof_ms=c[-1].close_ms+10)


def test_costs_decrease_performance_when_equal_fills():
    a = series()
    rules = {"BTCUSDT": SymbolRules("0.000001", 5)}
    low = replay_spot({"BTCUSDT": a}, rules, cfg(fee_bps=0, slippage_bps=0, half_spread_bps=0))
    high = replay_spot({"BTCUSDT": a}, rules, cfg(fee_bps=20, slippage_bps=10, half_spread_bps=5))
    assert high.final_equity < low.final_equity


def test_invalid_caps_fail_closed():
    with pytest.raises(ValueError):
        cfg(max_asset_weight=.8, max_total_weight=.6)


def test_misaligned_assets_rejected():
    a = series()
    b = series(base=1700000001000)
    with pytest.raises(ValueError, match="align"):
        replay_spot({"A": a, "B": b}, {"A": SymbolRules("0.1",5), "B": SymbolRules("0.1",5)}, cfg())
