# -*- coding: utf-8 -*-
"""Shared macro series extraction for RQ2 calibration and EXP_002/008.

Unit conventions match the World stochastic feature space (fractional rates,
not percent points), so calibrate_world and moment evaluation stay aligned.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# Calibrator column name -> World FeatureProcess name
CALIBRATOR_TO_FEATURE: dict[str, str] = {
    "fed_funds_effective_rate": "interest_rate",
    "consumer_price_index_yoy": "inflation_cpi",
    "treasury_10y_yield": "yield_10y",
    "gdp_growth": "gdp_growth",
}

# Feature -> preferred BTC feature-store column(s)
FEATURE_COLUMNS: dict[str, list[str]] = {
    "interest_rate": ["macro_fed_funds_effective_rate", "macro_fed_funds_daily"],
    "yield_10y": ["macro_treasury_10y_yield"],
    "gdp_growth": ["macro_real_gdp_growth", "macro_wb_gdp_growth_pct"],
    "inflation_cpi": [
        "macro_wb_inflation_cpi_pct",
        "macro_consumer_price_index",
        "macro_core_cpi",
    ],
}


def _finite_arr(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float64)
    return a[np.isfinite(a)]


def collapse_consecutive_duplicates(arr: np.ndarray) -> np.ndarray:
    """Drop consecutive equal values (asof-filled HF panels → observation changes)."""
    a = _finite_arr(arr)
    if a.size <= 1:
        return a
    keep = np.ones(a.size, dtype=bool)
    keep[1:] = a[1:] != a[:-1]
    return a[keep]


def _to_fraction_if_pct(arr: np.ndarray, *, force_if_median_gt: float = 1.0) -> np.ndarray:
    a = _finite_arr(arr)
    if a.size == 0:
        return a
    if float(np.nanmedian(a)) > force_if_median_gt:
        return a / 100.0
    return a


def _yoy_from_index(index: np.ndarray, *, periods: int = 12) -> np.ndarray:
    """Approximate YoY inflation from a monthly-ish CPI index series."""
    x = np.asarray(index, dtype=np.float64)
    if x.size <= periods:
        return np.array([], dtype=np.float64)
    # Use unique chronological values to avoid HF panel duplication blowing YoY.
    # Keep order; collapse consecutive duplicates.
    keep = np.ones(x.size, dtype=bool)
    keep[1:] = x[1:] != x[:-1]
    u = x[keep]
    u = u[np.isfinite(u)]
    if u.size <= periods:
        return np.array([], dtype=np.float64)
    prev = u[:-periods]
    cur = u[periods:]
    valid = (prev > 1e-9) & np.isfinite(prev) & np.isfinite(cur)
    out = np.full(cur.shape, np.nan)
    out[valid] = cur[valid] / prev[valid] - 1.0
    return out[np.isfinite(out)]


def _first_present(df: pd.DataFrame, names: list[str]) -> str | None:
    for n in names:
        if n in df.columns:
            return n
    return None


def inflation_series_from_df(df: pd.DataFrame) -> np.ndarray:
    """Prefer YoY from CPI index (monthly variation); else WB CPI % YoY.

    Never use raw one-step diffs of the index (that produced unstable skew in RQ2).
    """
    for col in ("macro_consumer_price_index", "macro_core_cpi"):
        if col not in df.columns:
            continue
        yoy = _yoy_from_index(df[col].to_numpy(), periods=12)
        if yoy.size >= 8:
            return yoy
    if "macro_wb_inflation_cpi_pct" in df.columns:
        arr = _to_fraction_if_pct(df["macro_wb_inflation_cpi_pct"].to_numpy())
        arr = collapse_consecutive_duplicates(arr)
        if arr.size >= 8:
            return arr
        # Few annual levels: still return if >= 4 for mean locking (weak σ)
        if arr.size >= 4:
            return arr
    return np.array([], dtype=np.float64)


def empirical_macro_from_df(
    df: pd.DataFrame,
    *,
    collapse_duplicates: bool = False,
) -> dict[str, np.ndarray]:
    """Map feature-store columns → World feature names (fractional units).

    ``collapse_duplicates=True`` keeps observation *changes* only (for
    calibrate_world on asof-filled panels). EXP_002 moment eval may use either;
    default False preserves prior evaluation density, True for calibration export.
    """
    out: dict[str, np.ndarray] = {}

    for feat, cols in FEATURE_COLUMNS.items():
        if feat == "inflation_cpi":
            arr = inflation_series_from_df(df)
            # YoY-from-index is already change-dated; optional extra collapse
            if collapse_duplicates and arr.size:
                arr = collapse_consecutive_duplicates(arr)
            if arr.size >= 8:
                out[feat] = arr
            continue
        col = _first_present(df, cols)
        if col is None:
            continue
        raw = df[col].to_numpy(dtype=np.float64)
        if feat in {"interest_rate", "yield_10y"}:
            arr = _to_fraction_if_pct(raw)
        elif feat == "gdp_growth":
            arr = _to_fraction_if_pct(raw)
            if arr.size and float(np.nanmax(np.abs(arr))) > 1.0:
                arr = arr / 100.0
        else:
            arr = _finite_arr(raw)
        if collapse_duplicates:
            arr = collapse_consecutive_duplicates(arr)
        if arr.size >= 8:
            out[feat] = arr
    return out


def calibrator_panel_from_df(df: pd.DataFrame) -> pd.DataFrame:
    """Build a parquet-ready panel with calibrator column names (fractional units)."""
    emp = empirical_macro_from_df(df, collapse_duplicates=True)
    rev = {v: k for k, v in CALIBRATOR_TO_FEATURE.items()}
    # Write each series independently (different change-dates → pad with NaN in parquet
    # is awkward). Store as separate equal-length by left-align + NaN pad to max.
    if not emp:
        return pd.DataFrame()
    max_n = max(a.size for a in emp.values())
    cols: dict[str, np.ndarray] = {}
    for feat, arr in emp.items():
        cal_name = rev.get(feat)
        if cal_name is None:
            continue
        padded = np.full(max_n, np.nan, dtype=np.float64)
        padded[: arr.size] = arr
        cols[cal_name] = padded
    return pd.DataFrame(cols)


def panel_provenance(df: pd.DataFrame, source: str) -> dict[str, Any]:
    return {
        "source": source,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "means": {c: float(np.nanmean(df[c].to_numpy())) for c in df.columns},
        "stds": {c: float(np.nanstd(df[c].to_numpy())) for c in df.columns},
        "fabricated": False,
    }
