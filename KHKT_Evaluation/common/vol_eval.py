# -*- coding: utf-8 -*-
"""Shared B0 / E1 / C1 volatility forecast evaluation.

Protocols
---------
- ``oos_beta`` (default): ŷ_E1 = ŷ_B0 + β · (L_lag(vol_mult) − 1), β/lag fit on
  train (first ``train_frac``), metrics reported on test holdout only. C1 shuffles
  the lagged regressor *after* β is fixed.
- ``legacy_mult``: ŷ_E1 = ŷ_B0 · (vol_mult / mean) on the full panel (historical).

When ``protocol='oos_beta'``, a ``protocol_legacy`` block is always attached for
scientific continuity with the crude multiplicative baseline.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

import numpy as np

from KHKT_Evaluation.common import data_loader, metrics, world_bridge


def _lag_series(x: np.ndarray, lag: int) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype=np.float64)
    if lag <= 0:
        out[:] = x
        return out
    out[lag:] = x[:-lag]
    return out


def _ols_beta(x: np.ndarray, y: np.ndarray) -> float:
    """Scalar OLS through origin-ish: y ~ beta * x (with intercept 0 on residual)."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 8:
        return 0.0
    xx = x[mask]
    yy = y[mask]
    denom = float(np.dot(xx, xx))
    if denom < 1e-18:
        return 0.0
    return float(np.dot(xx, yy) / denom)


def _valid_mask(*arrays: np.ndarray) -> np.ndarray:
    m = np.ones(arrays[0].shape[0], dtype=bool)
    for a in arrays:
        m &= np.isfinite(a)
    return m


def _legacy_mult_forecasts(
    y_b0: np.ndarray,
    vol_mult: np.ndarray,
    *,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    finite = vol_mult[np.isfinite(vol_mult)]
    mean_m = float(np.mean(finite)) if finite.size else 1.0
    if mean_m <= 0:
        mean_m = 1.0
    scale = vol_mult / mean_m
    y_e1 = y_b0 * scale
    rng = np.random.default_rng(seed)
    scale_rand = scale.copy()
    rng.shuffle(scale_rand)
    y_c1 = y_b0 * scale_rand
    stats = {
        "mean": float(np.mean(finite)) if finite.size else None,
        "std": float(np.std(finite)) if finite.size else None,
        "min": float(np.min(finite)) if finite.size else None,
        "max": float(np.max(finite)) if finite.size else None,
        "unique_approx": int(len({round(x, 4) for x in finite.tolist()})) if finite.size else 0,
    }
    return y_e1, y_c1, stats


def _compare(m_b0: dict, m_e1: dict, m_c1: dict) -> dict[str, Any]:
    def _ok(*xs):
        return all(x is not None and np.isfinite(x) for x in xs)

    def improv(base, exp):
        if not _ok(base, exp) or base == 0:
            return None
        return float((base - exp) / abs(base) * 100.0)

    return {
        "mae_improvement_pct_e1_vs_b0": improv(m_b0["mae"], m_e1["mae"]),
        "rmse_improvement_pct_e1_vs_b0": improv(m_b0["rmse"], m_e1["rmse"]),
        "e1_better_mae_than_b0": bool(m_e1["mae"] < m_b0["mae"]) if _ok(m_b0["mae"], m_e1["mae"]) else None,
        "e1_better_mae_than_c1": bool(m_e1["mae"] < m_c1["mae"]) if _ok(m_e1["mae"], m_c1["mae"]) else None,
        "passes_random_control": bool(m_e1["mae"] < m_c1["mae"]) if _ok(m_e1["mae"], m_c1["mae"]) else None,
        "directional_accuracy_delta_e1_b0": (
            None
            if not _ok(m_b0["directional_accuracy"], m_e1["directional_accuracy"])
            else float(m_e1["directional_accuracy"] - m_b0["directional_accuracy"])
        ),
    }


def _score_slice(
    y_true: np.ndarray,
    y_b0: np.ndarray,
    y_e1: np.ndarray,
    y_c1: np.ndarray,
    mask: np.ndarray | None = None,
) -> dict[str, Any]:
    if mask is None:
        mask = _valid_mask(y_true, y_b0, y_e1, y_c1)
    yt, yb, ye, yc = y_true[mask], y_b0[mask], y_e1[mask], y_c1[mask]
    m_b0 = metrics.summarize_vol_forecast(yt, yb, label="B0_market_only")
    m_e1 = metrics.summarize_vol_forecast(yt, ye, label="E1_world_coupled")
    m_c1 = metrics.summarize_vol_forecast(yt, yc, label="C1_random_control")
    return {
        "metrics": {"B0": m_b0, "E1": m_e1, "C1": m_c1},
        "comparison": _compare(m_b0, m_e1, m_c1),
        "n": int(mask.sum()),
        "aux": {
            "delta_vol_mae": {
                "B0": metrics.delta_vol_mae(yt, yb),
                "E1": metrics.delta_vol_mae(yt, ye),
                "C1": metrics.delta_vol_mae(yt, yc),
            },
            "regime_directional_accuracy": {
                "B0": metrics.regime_directional_accuracy(yt, yb),
                "E1": metrics.regime_directional_accuracy(yt, ye),
                "C1": metrics.regime_directional_accuracy(yt, yc),
            },
        },
    }


def evaluate_b0_e1_c1(
    df,
    *,
    hist_vol_window: int = 60,
    fwd_vol_horizon: int = 60,
    macro_sample_every: int = 10,
    seed: int = 42,
    protocol: str = "oos_beta",
    train_frac: float = 0.70,
    lag_candidates: Sequence[int] = (0, 1, 2, 3, 5),
    fixed_beta: float | None = None,
    fixed_lag: int | None = None,
    fit_on_full_for_event: bool = False,
) -> dict[str, Any]:
    """Evaluate B0/E1/C1 under the chosen protocol.

    If ``fixed_beta`` / ``fixed_lag`` are set, skip train search (used by event
    windows that must reuse globally fit coefficients — no leakage).
    """
    returns = data_loader.price_log_returns(df["price"].to_numpy())
    y_true = metrics.forward_realized_vol(returns, fwd_vol_horizon)
    y_b0 = metrics.realized_vol(returns, hist_vol_window)
    vol_mult = world_bridge.vol_multipliers_for_panel(df, sample_every=macro_sample_every)

    # Always compute legacy multiplicative on full valid panel for continuity.
    y_e1_leg, y_c1_leg, vol_stats = _legacy_mult_forecasts(y_b0, vol_mult, seed=seed)
    legacy_full = _score_slice(y_true, y_b0, y_e1_leg, y_c1_leg)

    n = len(y_true)
    train_n = int(max(8, min(n - 8, round(n * float(train_frac)))))
    train_mask = np.zeros(n, dtype=bool)
    test_mask = np.zeros(n, dtype=bool)
    train_mask[:train_n] = True
    test_mask[train_n:] = True
    # Purge train labels whose forward window [t+1, t+horizon] crosses into test.
    # forward_realized_vol(t) uses returns up to index t+horizon; require t+horizon < train_n.
    horizon = int(fwd_vol_horizon)
    purge_lo = max(0, train_n - horizon)
    train_mask[purge_lo:train_n] = False
    boundary_purge = {
        "fwd_vol_horizon": horizon,
        "train_n_split": int(train_n),
        "purge_lo": int(purge_lo),
        "n_train_after_purge": int(train_mask.sum()),
        "n_test_split": int(test_mask.sum()),
        "note": (
            "Excluded train indices whose 60-step (horizon) forecast window "
            "crosses the train/test boundary; test set unchanged."
        ),
    }

    if protocol == "legacy_mult":
        out = {
            "protocol": "legacy_mult",
            "y_true": y_true,
            "y_b0": y_b0,
            "y_e1": y_e1_leg,
            "y_c1": y_c1_leg,
            "vol_mult": vol_mult,
            "vol_multiplier_stats": vol_stats,
            "metrics": legacy_full["metrics"],
            "comparison": legacy_full["comparison"],
            "aux": legacy_full["aux"],
            "train_frac": None,
            "n_train": None,
            "n_test": int(legacy_full["n"]),
            "beta": None,
            "lag": None,
            "fingerprint": _fingerprint(
                protocol="legacy_mult", seed=seed, beta=None, lag=None, train_frac=None
            ),
            "protocol_legacy": {
                "metrics": legacy_full["metrics"],
                "comparison": legacy_full["comparison"],
                "note": "Same as primary under protocol=legacy_mult",
            },
        }
        return out

    # --- oos_beta ---
    # Regressor: deviation of lagged vol_mult from *train* mean (vol_mult is not
    # centered at 1.0 in macro_to_micro — using raw (m-1) collapses beta≈0).
    resid = y_true - y_b0
    best_lag = int(fixed_lag) if fixed_lag is not None else 0
    best_beta = float(fixed_beta) if fixed_beta is not None else 0.0
    best_train_mae = float("inf")
    lag_search: list[dict[str, Any]] = []
    train_mean_m = 1.0

    search_mask = np.ones(n, dtype=bool) if fit_on_full_for_event else train_mask
    finite_train_m = vol_mult[search_mask & np.isfinite(vol_mult)]
    if finite_train_m.size:
        train_mean_m = float(np.mean(finite_train_m))
        if train_mean_m <= 0:
            train_mean_m = 1.0

    def _regressor(lag: int) -> np.ndarray:
        return _lag_series(vol_mult, int(lag)) / train_mean_m - 1.0

    if fixed_beta is None or fixed_lag is None:
        for lag in lag_candidates:
            x = _regressor(lag)
            m = search_mask & _valid_mask(x, resid, y_b0)
            beta = _ols_beta(x[m], resid[m])
            y_hat = y_b0 + beta * x
            mae_tr = metrics.mae(y_true[m], y_hat[m])
            lag_search.append({"lag": int(lag), "beta": beta, "train_mae": mae_tr})
            if np.isfinite(mae_tr) and mae_tr < best_train_mae:
                best_train_mae = float(mae_tr)
                best_lag = int(lag)
                best_beta = float(beta)
    else:
        best_lag = int(fixed_lag)
        best_beta = float(fixed_beta)

    x_best = _regressor(best_lag)
    y_e1 = y_b0 + best_beta * x_best
    rng = np.random.default_rng(seed)
    x_shuf = x_best.copy()
    finite_idx = np.where(np.isfinite(x_shuf))[0]
    shuffled = x_shuf[finite_idx].copy()
    rng.shuffle(shuffled)
    x_shuf[finite_idx] = shuffled
    y_c1 = y_b0 + best_beta * x_shuf
    # Primary metrics: TEST holdout only (scientific OOS)
    test_valid = test_mask & _valid_mask(y_true, y_b0, y_e1, y_c1)
    scored = _score_slice(y_true, y_b0, y_e1, y_c1, test_valid)

    # Legacy on the *same* test mask for fair historical contrast
    legacy_test = _score_slice(y_true, y_b0, y_e1_leg, y_c1_leg, test_valid)

    fp = _fingerprint(
        protocol="oos_beta",
        seed=seed,
        beta=best_beta,
        lag=best_lag,
        train_frac=train_frac,
    )

    return {
        "protocol": "oos_beta",
        "y_true": y_true,
        "y_b0": y_b0,
        "y_e1": y_e1,
        "y_c1": y_c1,
        "vol_mult": vol_mult,
        "vol_multiplier_stats": vol_stats,
        "metrics": scored["metrics"],
        "comparison": scored["comparison"],
        "aux": scored["aux"],
        "train_frac": float(train_frac),
        "n_train": int((train_mask & _valid_mask(y_true, y_b0)).sum()),
        "n_test": int(scored["n"]),
        "boundary_purge": boundary_purge,
        "beta": best_beta,
        "lag": best_lag,
        "train_mean_vol_mult": train_mean_m,
        "train_mae_selected": None if best_train_mae == float("inf") else best_train_mae,
        "lag_search": lag_search,
        "fingerprint": fp,
        "test_mask": test_mask,
        "train_mask": train_mask,
        "protocol_legacy": {
            "formula": "E1 = B0 * (vol_mult / mean); C1 = B0 * shuffle(scale)",
            "metrics_full_panel": legacy_full["metrics"],
            "comparison_full_panel": legacy_full["comparison"],
            "metrics_test_mask": legacy_test["metrics"],
            "comparison_test_mask": legacy_test["comparison"],
        },
    }


def score_event_windows(
    df,
    ev: dict[str, Any],
    windows: list[dict[str, Any]],
    *,
    ts_col: str = "ts_ms",
    restrict_to_test: bool = False,
) -> list[dict[str, Any]]:
    """Score pre-fit OOS forecasts on event sub-windows (no β refit)."""
    ts = df[ts_col].to_numpy()
    rows = []
    for w in windows:
        mask = (ts >= int(w["start_ts_ms"])) & (ts <= int(w["end_ts_ms"]))
        if restrict_to_test and "test_mask" in ev:
            mask = mask & ev["test_mask"]
        scored = _score_slice(ev["y_true"], ev["y_b0"], ev["y_e1"], ev["y_c1"], mask)
        rows.append(
            {
                "phase": w.get("phase", "during"),
                "start_ts_ms": int(w["start_ts_ms"]),
                "end_ts_ms": int(w["end_ts_ms"]),
                **scored,
            }
        )
    return rows


def _fingerprint(*, protocol: str, seed: int, beta: float | None, lag: int | None, train_frac: float | None) -> str:
    payload = {
        "protocol": protocol,
        "seed": seed,
        "beta": None if beta is None else round(float(beta), 12),
        "lag": lag,
        "train_frac": train_frac,
    }
    raw = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
