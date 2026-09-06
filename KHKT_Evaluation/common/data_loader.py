# -*- coding: utf-8 -*-
"""Data loading helpers for KHKT experiments."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from KHKT_Evaluation.common.paths import FEATURES_BTC, FEATURES_BTC_ALT


def resolve_btc_features() -> Path:
    if FEATURES_BTC.exists():
        return FEATURES_BTC
    if FEATURES_BTC_ALT.exists():
        return FEATURES_BTC_ALT
    raise FileNotFoundError(
        f"BTC feature parquet missing. Expected {FEATURES_BTC} or {FEATURES_BTC_ALT}"
    )


def load_btc_panel(
    *,
    max_rows: int | None = 40_000,
    stride: int = 5,
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """Load BTC features with optional downsampling for reproducible experiments.

    ``stride`` keeps every N-th row after sort (reduces runtime without fabricating data).
    """
    path = resolve_btc_features()
    usecols = columns
    df = pd.read_parquet(path, columns=usecols)
    if "ts_ms" in df.columns:
        df = df.sort_values("ts_ms").reset_index(drop=True)
    if stride > 1:
        df = df.iloc[::stride].reset_index(drop=True)
    if max_rows is not None and len(df) > max_rows:
        # keep the most recent window (still real data)
        df = df.iloc[-max_rows:].reset_index(drop=True)
    return df


def price_log_returns(price: np.ndarray) -> np.ndarray:
    p = np.asarray(price, dtype=np.float64)
    out = np.full(p.shape, np.nan)
    if p.size < 2:
        return out
    prev = p[:-1]
    cur = p[1:]
    valid = np.isfinite(prev) & np.isfinite(cur) & (prev > 0) & (cur > 0)
    ratio = np.empty_like(cur)
    ratio[:] = np.nan
    ratio[valid] = cur[valid] / prev[valid]
    out[1:] = np.where(valid, np.log(ratio), np.nan)
    return out


# Canonical BTC feature store uses ``indicator_obi`` (not bare ``obi``).
OBI_COLUMN_CANDIDATES: tuple[str, ...] = ("indicator_obi", "obi", "order_book_imbalance")


def resolve_obi_column(df: pd.DataFrame) -> str | None:
    """Return the first present OBI column name, preferring ``indicator_obi``."""
    for col in OBI_COLUMN_CANDIDATES:
        if col in df.columns:
            return col
    return None


def position_signal_from_panel(
    df: pd.DataFrame,
    rets: np.ndarray,
) -> dict[str, Any]:
    """Build ±1 positions from real OBI when available; else lagged return sign.

    Always records provenance so reports cannot claim ``sign(OBI)`` after a silent fallback.
    """
    col = resolve_obi_column(df)
    if col is not None:
        raw = df[col].to_numpy(dtype=np.float64)
        pos = np.sign(np.nan_to_num(raw, nan=0.0))
        return {
            "positions": pos,
            "obi_column": col,
            "signal_source": "sign_obi",
            "used_real_obi": True,
        }
    pos = np.sign(np.nan_to_num(rets, nan=0.0))
    pos = np.roll(pos, 1)
    pos[0] = 0.0
    return {
        "positions": pos,
        "obi_column": None,
        "signal_source": "lagged_return_sign_fallback",
        "used_real_obi": False,
    }


def dataset_provenance(df: pd.DataFrame, path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "rows": int(len(df)),
        "columns": list(df.columns),
        "ts_min": int(df["ts_ms"].min()) if "ts_ms" in df.columns and len(df) else None,
        "ts_max": int(df["ts_ms"].max()) if "ts_ms" in df.columns and len(df) else None,
        "fabricated": False,
    }
