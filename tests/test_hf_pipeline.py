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


def test_deploy_gate_accepts_swing_forward_col(tmp_path: Path) -> None:
    from training.deploy_gate import run_holdout_backtest

    df = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"] * 80,
            "ts_ms": list(range(80)),
            "price": [100.0 + i * 0.1 for i in range(80)],
            "forward_return_1h": [0.001 if i % 2 == 0 else -0.001 for i in range(80)],
        }
    )
    path = tmp_path / "quant_holdout.parquet"
    df.to_parquet(path, index=False)
    metrics = run_holdout_backtest(path)
    assert metrics["signal_col"] == "forward_return_1h"
    assert metrics["rows"] >= 1


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
