"""Temporal-safety regression checks for ECONITH research-only train/backtest flows."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def _dataset(tmp_path, *, btc_rows=60, eth_rows=48, mutate_holdout=False):
    from training.quant.label_symbol import label_dataset

    start = 1_700_000_000_000
    records = []
    for symbol, length, base in (
        ("BTCUSDT", btc_rows, 100.0),
        ("ETHUSDT", eth_rows, 200.0),
    ):
        for i in range(length):
            price = base + i * 0.1
            if mutate_holdout and i >= length * 0.8:
                price = base + 1000 + i * 30
            records.append({
                "symbol": symbol, "ts_ms": start + i * 60_000,
                "price": price, "mid": price,
            })
    root = tmp_path / "features"
    root.mkdir(parents=True)
    pd.DataFrame(records).to_parquet(root / "features_00000.parquet", index=False)
    output = tmp_path / "quant_labeled.parquet"
    summary = label_dataset(
        str(root), str(output), 0.25, horizon_preset="scalp",
    )
    train = pd.read_parquet(output)
    holdout = pd.read_parquet(summary["holdout_output"])
    return train, holdout, summary


def test_purge_checks_all_finite_horizons_and_global_first_holdout(tmp_path):
    train, holdout, summary = _dataset(tmp_path)
    cutoff = int(holdout.ts_ms.min())
    assert summary["purged_train_rows"] > 0
    assert summary["first_holdout_ts_ms"] == cutoff
    assert not train.empty and not holdout.empty
    for col, duration in (
        ("forward_return_1m", 60_000),
        ("forward_return_5m", 300_000),
        ("forward_return_15m", 900_000),
    ):
        eligible = train[col].notna()
        assert (train.loc[eligible, "ts_ms"] + duration < cutoff).all()
    assert (train.ts_ms < cutoff).all()


def test_purge_train_invariant_to_holdout_future_prices(tmp_path):
    earlier, _, _ = _dataset(tmp_path / "first", mutate_holdout=False)
    poisoned, _, _ = _dataset(tmp_path / "second", mutate_holdout=True)
    pd.testing.assert_frame_equal(earlier, poisoned)


def test_purge_respects_actual_target_timestamp_with_gapped_trades(tmp_path):
    from training.quant.label_symbol import label_dataset

    # No events between minutes 20 and 45. A horizon from minute 19 can
    # resolve to minute 45, despite 19+15 < the holdout boundary.
    times = list(range(20)) + list(range(60, 100))
    df = pd.DataFrame({
        "symbol": ["BTCUSDT"] * len(times),
        "ts_ms": [1_700_000_000_000 + t * 60_000 for t in times],
        "price": [100.0 + i for i in range(len(times))],
    })
    root = tmp_path / "source"
    root.mkdir()
    df.to_parquet(root / "features_00000.parquet", index=False)
    dest = tmp_path / "train.parquet"
    summary = label_dataset(str(root), str(dest), 0.66, horizon_preset="scalp")
    train = pd.read_parquet(dest)
    held = pd.read_parquet(summary["holdout_output"])
    cutoff = int(held.ts_ms.iloc[0])
    assert cutoff == 1_700_000_000_000 + 60 * 60_000
    assert summary["purged_train_rows"] > 0
    assert (train["ts_ms"] < 1_700_000_000_000 + 19 * 60_000).all()
    # For every retained train label, verify the actual time of the trade used
    # to compute each forward return (not merely ts_ms+horizon).
    market_times = df.ts_ms.to_numpy(dtype="int64")
    for _, row in train.iterrows():
        for col, horizon in (
            ("forward_return_1m", 60_000),
            ("forward_return_5m", 300_000),
            ("forward_return_15m", 900_000),
        ):
            if pd.notna(row[col]):
                ix = np.searchsorted(market_times, int(row.ts_ms) + horizon)
                assert ix < len(market_times)
                assert market_times[ix] < cutoff


@pytest.mark.parametrize("ratio", [-1, 0, 1, 1.5, float("nan")])
def test_invalid_holdout_ratios_fail_closed(tmp_path, ratio):
    from training.quant.label_symbol import label_dataset

    with pytest.raises(ValueError, match="holdout_ratio"):
        label_dataset(str(tmp_path), str(tmp_path / "out.parquet"), ratio)


def test_backtest_strategy_cannot_read_labels_or_rewards():
    from training.evaluation.backtest import BacktestEngine, BacktestConfig

    frame = pd.DataFrame({
        "symbol": ["BTCUSDT"] * 4,
        "ts_ms": [1, 2, 3, 4],
        "price": [100.0, 101.0, 102.0, 103.0],
        "forward_return_1m": [0.1, -0.1, 0.1, -0.1],
        "forward_return_5m": [-0.2, 0.2, -0.2, 0.2],
        "reward": [9.0] * 4,
        "target": [1.0] * 4,
        "label_end": [4, 5, 6, 7],
        "target_regime": [1] * 4,
        "future_volatility": [2.0] * 4,
        "oos_prediction_1m": [0.02] * 4,
    })
    observed = []
    def signal_fn(visible):
        observed.append(set(visible.columns))
        assert "price" in visible and "oos_prediction_1m" in visible
        for name in (
            "forward_return_1m", "forward_return_5m", "reward",
            "target", "label_end", "target_regime", "future_volatility",
        ):
            assert name not in visible.columns
        visible.loc[:, "price"] = -99  # Mutating a view cannot alter evaluator.
        return np.ones(len(visible))

    result = BacktestEngine(BacktestConfig(
        fee_bps=0, slippage_bps=0, spread_bps=0,
    )).run_frame(frame, signal_fn)
    assert result.rows == len(frame)
    assert np.allclose(result.net_returns, frame.forward_return_1m)
    assert (frame.price > 0).all()
    assert len(observed) == 1


def test_backtest_oracle_lookup_fails_closed():
    from training.evaluation.backtest import BacktestEngine

    frame = pd.DataFrame({
        "symbol": ["BTCUSDT"] * 3,
        "ts_ms": [1, 2, 3],
        "price": [100.0, 101.0, 102.0],
        "forward_return_1m": [0.1, -0.2, 0.3],
    })
    with pytest.raises(KeyError, match="forward_return_1m"):
        BacktestEngine().run_frame(
            frame, lambda rows: np.sign(rows["forward_return_1m"].to_numpy()),
        )
