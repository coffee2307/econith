"""Tests for HF feature batch append + holdout naming + deploy gate columns."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import polars as pl
import pytest


def test_hf_map_ppo_contract_aliases() -> None:
    from training.hf_features import _map_ppo_contract

    hf = pl.DataFrame(
        {
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "ts_ms": [1_700_000_000_000, 1_700_000_060_000],
            "indicator_obi": [0.6, 0.4],
            "indicator_trade_imbalance": [0.2, -0.1],
            "trade_volume": [10.0, 20.0],
            "buy_volume": [6.0, 8.0],
            "sell_volume": [4.0, 12.0],
            "funding_rate": [0.0001, 0.0002],
        }
    )
    out = _map_ppo_contract(hf)
    assert "obi" in out.columns and "volume_delta" in out.columns
    assert "trade_count" in out.columns and "time_to_funding_s" in out.columns
    assert out["obi"].to_list() == [0.6, 0.4]
    assert out["volume_delta"].to_list() == pytest.approx([0.2, -0.1])
    assert out["open_interest"].to_list() == [0.0, 0.0]


def test_hf_batch_concat_writes_features_prefix(tmp_path: Path) -> None:
    """Simulate two batches for one symbol — final file must contain both."""
    from training.hf_features import _concat

    a = pl.DataFrame(
        {"symbol": ["BTCUSDT"], "ts_ms": [100], "obi": [0.5], "price": [1.0]}
    )
    b = pl.DataFrame(
        {"symbol": ["BTCUSDT"], "ts_ms": [200], "obi": [0.6], "price": [1.1]}
    )
    combined = _concat([a, b]).unique(subset=["symbol", "ts_ms"], keep="last")
    out = tmp_path / "features_BTCUSDT.parquet"
    combined.write_parquet(out)
    loaded = pl.read_parquet(out)
    assert loaded.height == 2
    assert out.name.startswith("features_")


def test_label_holdout_suffix_hf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from training.quant import label_symbol as ls

    rows = 40
    df = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * rows,
            "ts_ms": [1_700_000_000_000 + i * 60_000 for i in range(rows)],
            "price": [100.0 + i * 0.01 for i in range(rows)],
            "obi": [0.5] * rows,
        }
    )
    feat_dir = tmp_path / "features_hf"
    feat_dir.mkdir()
    df.to_parquet(feat_dir / "features_BTCUSDT.parquet", index=False)

    out = tmp_path / "processed" / "quant_labeled_hf.parquet"
    summary = ls.label_dataset(
        str(feat_dir),
        str(out),
        holdout_ratio=0.25,
        horizon_preset="scalp",
    )
    holdout = Path(summary["holdout_output"])
    assert holdout.name == "quant_holdout_hf.parquet"
    assert holdout.exists() and out.exists()
    assert "forward_return_1m" in pd.read_parquet(holdout).columns


def _holdout_frame():
    return pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 80,
            "ts_ms": [1_700_000_000_000 + i * 3_600_000 for i in range(80)],
            "price": [100.0 + i * 0.1 for i in range(80)],
            "forward_return_1h": [
                0.001 if i % 2 == 0 else -0.001 for i in range(80)
            ],
        }
    )


def test_deploy_gate_rejects_forward_label_as_signal(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = _holdout_frame()
    path = tmp_path / "quant_holdout.parquet"
    df.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="refusing look-ahead backtest"):
        run_holdout_backtest(path)


def test_deploy_gate_accepts_timestamped_oos_predictions(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = _holdout_frame()
    df["oos_prediction_1h"] = [
        0.002 if i % 3 == 0 else -0.002 for i in range(len(df))
    ]
    df["prediction_asof_ms"] = df["ts_ms"] - 1000
    df["prediction_model_id"] = "frozen-example-v1"
    path = tmp_path / "quant_holdout.parquet"
    df.to_parquet(path, index=False)
    metrics = run_holdout_backtest(path)
    assert metrics["signal_col"] == "oos_prediction_1h"
    assert metrics["realized_col"] == "forward_return_1h"
    assert metrics["rows"] == 80
    assert metrics["prediction_model_id"] == ["frozen-example-v1"]
    assert metrics["periods_per_year"] == 8760


def test_deploy_gate_rejects_future_predictions(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = _holdout_frame()
    df["oos_prediction_1h"] = [0.002] * len(df)
    df["prediction_asof_ms"] = df["ts_ms"] + 1
    df["prediction_model_id"] = "leaky"
    path = tmp_path / "future_holdout.parquet"
    df.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="in the future"):
        run_holdout_backtest(path)


def test_deploy_gate_rejects_copied_oracle(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = _holdout_frame()
    df["oos_prediction_1h"] = df["forward_return_1h"]
    df["prediction_asof_ms"] = df["ts_ms"]
    df["prediction_model_id"] = "oracle"
    path = tmp_path / "oracle_holdout.parquet"
    df.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="reproduces realized"):
        run_holdout_backtest(path)


def test_deploy_gate_rejects_overlapping_horizon(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = _holdout_frame()
    df["ts_ms"] = [1_700_000_000_000 + i * 60_000 for i in range(len(df))]
    df["oos_prediction_1h"] = [0.002] * len(df)
    df["prediction_asof_ms"] = df["ts_ms"]
    df["prediction_model_id"] = "frozen-example-v1"
    path = tmp_path / "overlapping.parquet"
    df.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="overlapping"):
        run_holdout_backtest(path)


def test_deploy_gate_rejects_unaligned_multiasset_results(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = _holdout_frame()
    df.loc[40:, "symbol"] = "ETHUSDT"
    df["oos_prediction_1h"] = [0.002] * len(df)
    df["prediction_asof_ms"] = df["ts_ms"]
    df["prediction_model_id"] = "frozen-example-v1"
    path = tmp_path / "multiasset.parquet"
    df.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="multi-asset"):
        run_holdout_backtest(path)


def test_deploy_gate_requires_oos_provenance() -> None:
    from training.deploy_gate import evaluate_gate

    ok, reason = evaluate_gate(
        {"rows": 5000, "annualized_sharpe": 99, "max_drawdown": 0}
    )
    assert not ok
    assert "out-of-sample" in reason


def test_deploy_gate_rejects_unverified_external_reports(tmp_path: Path) -> None:
    from training.deploy_gate import gate_or_raise

    path = tmp_path / "report.json"
    path.write_text('{"annualized_sharpe": 100}', encoding="utf-8")
    with pytest.raises(SystemExit, match="standalone backtest reports"):
        gate_or_raise(
            holdout=None, metrics_report=path, min_sharpe=0.25,
            max_drawdown=0.30, min_rows=500, skip=False
        )


def test_deploy_gate_disallows_emergency_skip() -> None:
    from training.deploy_gate import gate_or_raise

    with pytest.raises(SystemExit, match="disabled"):
        gate_or_raise(
            holdout=None, metrics_report=None, min_sharpe=0.25,
            max_drawdown=0.30, min_rows=500, skip=True
        )

def test_resolve_realized_col_klines_swing() -> None:
    from training.train_ppo import _resolve_realized_col

    df = pd.DataFrame(
        {
            "forward_return_1h": [0.01],
            "forward_return_4h": [0.02],
            "forward_return_1d": [0.03],
        }
    )
    # trend prefers 5m (absent) → falls through to 4h/1h on klines profile
    assert _resolve_realized_col(df, "trend", "klines") == "forward_return_4h"
    assert _resolve_realized_col(df, "scalper", "klines") == "forward_return_1h"


def test_resolve_realized_col_hf_scalp() -> None:
    from training.train_ppo import _resolve_realized_col

    df = pd.DataFrame(
        {
            "forward_return_1m": [0.001],
            "forward_return_5m": [0.002],
            "forward_return_15m": [0.003],
        }
    )
    assert _resolve_realized_col(df, "scalper", "hf") == "forward_return_1m"
    assert _resolve_realized_col(df, "trend", "hf") == "forward_return_5m"


def test_causal_oos_baseline_never_reads_future_prices() -> None:
    from training.oos_baseline import generate_causal_baseline

    df = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 20,
            "ts_ms": [1_700_000_000_000 + i * 3_600_000 for i in range(20)],
            "price": [100.0 + i for i in range(20)],
            "forward_return_1h": [0.01] * 20,
        }
    )
    a = generate_causal_baseline(df, lookback=3)
    changed = df.copy()
    changed.loc[19, "price"] = 100_000.0
    changed.loc[19, "forward_return_1h"] = -99.0
    b = generate_causal_baseline(changed, lookback=3)
    assert a["oos_prediction_1h"].equals(b["oos_prediction_1h"])
    assert a["oos_prediction_1h"].iloc[:4].eq(0.0).all()
    assert a["prediction_asof_ms"].le(a["ts_ms"]).all()
    assert (a["prediction_model_id"] == "rule-lagged-momentum-v1-lookback3").all()


def test_causal_oos_baseline_keeps_assets_isolated() -> None:
    from training.oos_baseline import generate_causal_baseline

    btc = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 8,
            "ts_ms": [1_700_000_000_000 + i * 3_600_000 for i in range(8)],
            "price": [100.0 + i for i in range(8)],
            "forward_return_1h": [0.001] * 8,
        }
    )
    eth = btc.copy()
    eth["symbol"] = "ETHUSDT"
    eth["price"] = [200.0 - i for i in range(8)]
    out = generate_causal_baseline(pd.concat([btc, eth], ignore_index=True), lookback=2)
    btc_sig = out.loc[out["symbol"] == "BTCUSDT", "oos_prediction_1h"]
    eth_sig = out.loc[out["symbol"] == "ETHUSDT", "oos_prediction_1h"]
    assert btc_sig.iloc[3] > 0
    assert eth_sig.iloc[3] < 0
