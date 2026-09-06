# -*- coding: utf-8 -*-
"""Evaluation metrics for KHKT experiments (no fabricated defaults as results)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np


def _finite(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=np.float64).ravel()
    return a[np.isfinite(a)]


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _align(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.mean(np.abs(yt - yp)))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt, yp = _align(y_true, y_pred)
    if yt.size == 0:
        return float("nan")
    return float(np.sqrt(np.mean((yt - yp) ** 2)))


def mape(y_true: np.ndarray, y_pred: np.ndarray, eps: float = 1e-12) -> float:
    yt, yp = _align(y_true, y_pred)
    mask = np.abs(yt) > eps
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.abs((yt[mask] - yp[mask]) / yt[mask])))


def directional_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Sign agreement of first differences (change direction)."""
    yt, yp = _align(y_true, y_pred)
    if yt.size < 2:
        return float("nan")
    dy_t = np.sign(np.diff(yt))
    dy_p = np.sign(np.diff(yp))
    # ignore flat true moves
    mask = dy_t != 0
    if not np.any(mask):
        return float("nan")
    return float(np.mean(dy_t[mask] == dy_p[mask]))


def delta_vol_mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """MAE of first differences (Δvol around windows / regimes)."""
    yt, yp = _align(y_true, y_pred)
    if yt.size < 2:
        return float("nan")
    return float(np.mean(np.abs(np.diff(yt) - np.diff(yp))))


def regime_directional_accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Directional accuracy of vol regime shifts (sign of Δvol; flat true ignored)."""
    return directional_accuracy(y_true, y_pred)


def diebold_mariano(
    y_true: np.ndarray,
    y_pred_a: np.ndarray,
    y_pred_b: np.ndarray,
    *,
    h: int = 1,
    loss: str = "abs",
) -> dict[str, float]:
    """Diebold-Mariano test comparing forecast A vs B (negative DM ⇒ A better).

    Loss differential d_t = L(e_a) - L(e_b). Newey-West HAC variance with
    truncation lag ``h-1`` (typical when forecasts are h-step).
    """
    yt = np.asarray(y_true, dtype=np.float64).ravel()
    ya = np.asarray(y_pred_a, dtype=np.float64).ravel()
    yb = np.asarray(y_pred_b, dtype=np.float64).ravel()
    n = min(yt.size, ya.size, yb.size)
    yt, ya, yb = yt[:n], ya[:n], yb[:n]
    mask = np.isfinite(yt) & np.isfinite(ya) & np.isfinite(yb)
    yt, ya, yb = yt[mask], ya[mask], yb[mask]
    if yt.size < 16:
        return {
            "dm_stat": float("nan"),
            "p_value_two_sided": float("nan"),
            "mean_loss_diff": float("nan"),
            "n": float(yt.size),
        }
    ea = yt - ya
    eb = yt - yb
    if loss == "sq":
        d = ea**2 - eb**2
    else:
        d = np.abs(ea) - np.abs(eb)
    d_bar = float(d.mean())
    # Newey-West
    lag = max(0, int(h) - 1)
    gamma0 = float(np.mean((d - d_bar) ** 2))
    var = gamma0
    for j in range(1, lag + 1):
        w = 1.0 - j / (lag + 1.0)
        cov = float(np.mean((d[j:] - d_bar) * (d[:-j] - d_bar)))
        var += 2.0 * w * cov
    var = max(var, 1e-18) / d.size
    se = math.sqrt(var)
    dm = d_bar / se if se > 0 else float("nan")
    # two-sided normal approx
    try:
        from math import erf

        p = float(2.0 * (1.0 - 0.5 * (1.0 + erf(abs(dm) / math.sqrt(2.0)))))
    except Exception:  # noqa: BLE001
        p = float("nan")
    return {
        "dm_stat": float(dm),
        "p_value_two_sided": p,
        "mean_loss_diff": d_bar,
        "n": float(d.size),
        "hac_lag": float(lag),
    }


def moment_errors(empirical: np.ndarray, simulated: np.ndarray) -> dict[str, float]:
    e = _finite(empirical)
    s = _finite(simulated)
    if e.size < 2 or s.size < 2:
        return {
            "mean_error": float("nan"),
            "variance_error": float("nan"),
            "std_error": float("nan"),
            "skew_error": float("nan"),
            "kurtosis_error": float("nan"),
        }
    em, sm = float(e.mean()), float(s.mean())
    ev, sv = float(e.var()), float(s.var())
    es, ss = float(e.std()), float(s.std())
    return {
        "mean_error": sm - em,
        "variance_error": sv - ev,
        "std_error": ss - es,
        "skew_error": float(_skew(s) - _skew(e)),
        "kurtosis_error": float(_kurt(s) - _kurt(e)),
        "abs_mean_error": abs(sm - em),
        "abs_variance_error": abs(sv - ev),
        "moment_l1": abs(sm - em) + abs(ss - es) + abs(_skew(s) - _skew(e)),
    }


def max_drawdown(equity: np.ndarray) -> float:
    eq = _finite(equity)
    if eq.size == 0:
        return float("nan")
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1e-12)
    return float(np.max(dd))


def realized_vol(returns: np.ndarray, window: int) -> np.ndarray:
    r = np.asarray(returns, dtype=np.float64)
    out = np.full(r.shape, np.nan)
    if window < 2 or r.size < window:
        return out
    # rolling std
    c1 = np.cumsum(np.nan_to_num(r, nan=0.0))
    c2 = np.cumsum(np.nan_to_num(r, nan=0.0) ** 2)
    n = np.arange(1, r.size + 1)
    for i in range(window - 1, r.size):
        j = i - window + 1
        s1 = c1[i] - (c1[j - 1] if j > 0 else 0.0)
        s2 = c2[i] - (c2[j - 1] if j > 0 else 0.0)
        m = window
        var = max(0.0, s2 / m - (s1 / m) ** 2)
        out[i] = math.sqrt(var)
    return out


def forward_realized_vol(returns: np.ndarray, horizon: int) -> np.ndarray:
    """Std of *future* `horizon` returns at each t (uses t+1..t+horizon)."""
    r = np.asarray(returns, dtype=np.float64)
    n = r.size
    out = np.full(n, np.nan)
    if horizon < 2:
        return out
    for t in range(0, n - horizon):
        seg = r[t + 1 : t + 1 + horizon]
        if np.isfinite(seg).sum() >= max(2, horizon // 2):
            out[t] = float(np.nanstd(seg))
    return out


def _align(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    a = np.asarray(a, dtype=np.float64).ravel()
    b = np.asarray(b, dtype=np.float64).ravel()
    n = min(a.size, b.size)
    a, b = a[:n], b[:n]
    mask = np.isfinite(a) & np.isfinite(b)
    return a[mask], b[mask]


def _skew(x: np.ndarray) -> float:
    x = _finite(x)
    if x.size < 3:
        return float("nan")
    m = x.mean()
    s = x.std()
    if s < 1e-15:
        return 0.0
    return float(np.mean(((x - m) / s) ** 3))


def _kurt(x: np.ndarray) -> float:
    x = _finite(x)
    if x.size < 4:
        return float("nan")
    m = x.mean()
    s = x.std()
    if s < 1e-15:
        return 0.0
    return float(np.mean(((x - m) / s) ** 4) - 3.0)


def summarize_vol_forecast(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    label: str,
) -> dict[str, Any]:
    return {
        "label": label,
        "n": int(_align(y_true, y_pred)[0].size),
        "mae": mae(y_true, y_pred),
        "rmse": rmse(y_true, y_pred),
        "mape": mape(y_true, y_pred),
        "directional_accuracy": directional_accuracy(y_true, y_pred),
    }
