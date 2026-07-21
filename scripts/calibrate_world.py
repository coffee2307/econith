#!/usr/bin/env python3
"""ECONITH :: scripts.calibrate_world — moment-matching calibrator.

Tunes the :class:`StochasticEngine` (OU + jump-diffusion) coefficients so the
simulated hub feature distributions match the **empirical moments** of real
historical data (FRED macro + market series). This is what pulls the synthetic
"Mini-World" toward real-world statistical behaviour: the right long-run mean,
volatility, skew (asymmetry) and kurtosis (fat tails / jump frequency).

Pipeline
--------
1. Load historical panel (placeholder: ``data/raw/macro_history.parquet``).
2. Map real series -> ECONITH feature names.
3. Per feature, estimate:
     * mu     — long-run level (AR(1) fixed point),
     * theta  — mean-reversion speed (AR(1) decay),
     * sigma  — diffusion vol from the *non-jump* increments,
     * jump_intensity / jump_mean / jump_std — from tail (|Δ| > k·σ) increments.
4. Write ``models/world/stochastic_coeffs.json`` consumable by
   ``StochasticEngine.from_coefficients(...)``.

Degrades gracefully: if pandas or the parquet file is missing, it synthesises a
demonstrative sample so the harness always produces a valid coefficients file
(useful for CI / first-run smoke tests).

Run:
    python scripts/calibrate_world.py \
        --input data/raw/macro_history.parquet \
        --output models/world/stochastic_coeffs.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
)
logger = logging.getLogger("econith.calibrate_world")

# Trading days per year — the dt the StochasticEngine integrates against.
_DT = 1.0 / 252.0
# Tail threshold (in σ) beyond which an increment is attributed to a jump.
_JUMP_K = 3.0

# Historical series column -> ECONITH feature name. Extend as the panel grows.
_SERIES_MAP: dict[str, str] = {
    "fed_funds_effective_rate": "interest_rate",
    "consumer_price_index_yoy": "inflation_cpi",
    "treasury_10y_yield": "yield_10y",
    "gdp_growth": "gdp_growth",
    "dollar_index_dxy": "fx_spot",
    "manufacturing_pmi": "manufacturing_pmi",
    "unrest_index": "social_unrest_index",
    "geopolitical_risk": "geopolitical_risk",
}

# Physical clamp bands mirrored from the StochasticEngine defaults.
_BOUNDS: dict[str, tuple[float, float]] = {
    "gdp_growth": (-0.20, 0.20),
    "inflation_cpi": (-0.05, 0.60),
    "interest_rate": (-0.01, 0.25),
    "yield_10y": (-0.02, 0.35),
    "fx_spot": (0.01, 1.0e6),
    "manufacturing_pmi": (30.0, 70.0),
    "social_unrest_index": (0.0, 1.0),
    "geopolitical_risk": (0.0, 1.0),
}


def _moments(x: np.ndarray) -> dict[str, float]:
    """Mean / std / skew / (excess) kurtosis, dependency-free."""
    x = x[np.isfinite(x)]
    n = x.size
    if n < 3:
        return {"mean": 0.0, "std": 0.0, "skew": 0.0, "kurt": 0.0}
    mean = float(x.mean())
    std = float(x.std(ddof=0))
    if std < 1e-12:
        return {"mean": mean, "std": 0.0, "skew": 0.0, "kurt": 0.0}
    z = (x - mean) / std
    skew = float((z ** 3).mean())
    kurt = float((z ** 4).mean() - 3.0)  # excess kurtosis
    return {"mean": mean, "std": std, "skew": skew, "kurt": kurt}


def _fit_ar1(level: np.ndarray) -> tuple[float, float]:
    """Fit X_t = a + b·X_{t-1}; return (theta, mu) under Euler-OU mapping.

    b = exp(-theta·dt) => theta = -ln(b)/dt ; mu = a / (1 - b).
    """
    x = level[np.isfinite(level)]
    if x.size < 8:
        return 0.1, float(np.nanmean(level)) if x.size else 0.0
    x0, x1 = x[:-1], x[1:]
    var0 = float(np.var(x0))
    if var0 < 1e-12:
        return 0.1, float(x.mean())
    b = float(np.cov(x0, x1, bias=True)[0, 1] / var0)
    b = min(0.9999, max(1e-4, b))          # keep stationary + positive
    a = float(x1.mean() - b * x0.mean())
    theta = -np.log(b) / _DT
    mu = a / (1.0 - b)
    return float(np.clip(theta, 1e-3, 50.0)), float(mu)


def _calibrate_feature(feature: str, series: np.ndarray) -> dict[str, float]:
    """Moment-match one feature's OU + jump parameters to a real series."""
    level = np.asarray(series, dtype=np.float64)
    level = level[np.isfinite(level)]
    theta, mu = _fit_ar1(level)

    diffs = np.diff(level)
    m = _moments(diffs)
    inc_std = m["std"]

    # Split increments into diffusion (bulk) vs jumps (tails beyond k·σ).
    if inc_std > 1e-12:
        tail_mask = np.abs(diffs - diffs.mean()) > _JUMP_K * inc_std
    else:
        tail_mask = np.zeros_like(diffs, dtype=bool)
    bulk = diffs[~tail_mask]
    tail = diffs[tail_mask]

    sigma = float(bulk.std(ddof=0) / np.sqrt(_DT)) if bulk.size > 2 else float(inc_std / np.sqrt(_DT))
    n = max(1, diffs.size)
    jump_intensity = float(tail.size / n / _DT) if tail.size else 0.01
    jump_mean = float(tail.mean()) if tail.size else 0.0
    jump_std = float(tail.std(ddof=0)) if tail.size > 1 else max(sigma * np.sqrt(_DT) * 3.0, 1e-6)

    lo, hi = _BOUNDS.get(feature, (float(level.min()), float(level.max())))
    coeffs = {
        "theta": round(theta, 6),
        "mu": round(mu, 6),
        "sigma": round(max(sigma, 1e-8), 8),
        "jump_intensity": round(min(jump_intensity, 5.0), 6),
        "jump_mean": round(jump_mean, 8),
        "jump_std": round(max(jump_std, 1e-8), 8),
        "lo": lo,
        "hi": hi,
    }
    logger.info(
        "calibrated %-20s mu=%.4g theta=%.3g sigma=%.4g  jumps λ=%.3g μ=%.4g σ=%.4g "
        "(emp skew=%.2f kurt=%.2f)",
        feature, mu, theta, sigma, jump_intensity, jump_mean, jump_std, m["skew"], m["kurt"],
    )
    return coeffs


def _load_panel(path: Path) -> dict[str, np.ndarray]:
    """Load the historical panel as ``{column: np.ndarray}``; synth on failure.

    Also accepts a directory of monthly macro JSONL snapshots under
    ``datasets/raw/macro/<source>/<YYYY-MM>.jsonl`` (live collector layout).
    """
    if path.is_dir():
        panel = _load_macro_jsonl_dir(path)
        if panel:
            return panel
        logger.warning("no usable macro JSONL under %s; using synthetic sample", path)
        return _synthetic_panel()
    if path.exists():
        try:
            import pandas as pd

            df = pd.read_parquet(path)
            logger.info("loaded %s (%d rows, %d cols)", path, len(df), df.shape[1])
            return {c: pd.to_numeric(df[c], errors="coerce").to_numpy("float64")
                    for c in df.columns}
        except Exception as exc:  # noqa: BLE001 - fall through to synthetic
            logger.warning("could not read %s (%s); using synthetic sample", path, exc)
    else:
        # Prefer live collector dumps when the placeholder parquet is absent.
        macro_dir = _ROOT / "datasets" / "raw" / "macro"
        if macro_dir.is_dir():
            panel = _load_macro_jsonl_dir(macro_dir)
            if panel:
                logger.info("calibrating from collector JSONL under %s", macro_dir)
                return panel
        logger.warning("input %s not found; using synthetic sample", path)
    return _synthetic_panel()


def _load_macro_jsonl_dir(root: Path) -> dict[str, np.ndarray]:
    """Best-effort extract of numeric feature series from collector JSONL."""
    series: dict[str, list[float]] = {}
    for path in sorted(root.rglob("*.jsonl")):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                feats = row.get("features") or row
                if not isinstance(feats, dict):
                    continue
                for key, val in feats.items():
                    if isinstance(val, (int, float)) and np.isfinite(val):
                        series.setdefault(str(key), []).append(float(val))
        except OSError:
            continue
    if not series:
        return {}
    # Remap common collector keys onto the calibrator series map when possible.
    aliases = {
        "FEDFUNDS": "fed_funds_effective_rate",
        "interest_rate": "fed_funds_effective_rate",
        "CPIAUCSL": "consumer_price_index_yoy",
        "inflation": "consumer_price_index_yoy",
        "gdp_growth": "gdp_growth",
        "DGS10": "treasury_10y_yield",
        "DTWEXBGS": "dollar_index_dxy",
    }
    out: dict[str, np.ndarray] = {}
    for k, vals in series.items():
        target = aliases.get(k, k)
        if len(vals) >= 8:
            out[target] = np.asarray(vals, dtype="float64")
    return out


def _synthetic_panel(n: int = 2_520, seed: int = 7) -> dict[str, np.ndarray]:
    """Deterministic OU-with-jumps sample so the harness always runs."""
    rng = np.random.default_rng(seed)
    out: dict[str, np.ndarray] = {}
    specs = {
        "fed_funds_effective_rate": (0.03, 0.08, 0.004, 0.01, 0.005, 0.004),
        "consumer_price_index_yoy": (0.025, 0.10, 0.006, 0.03, 0.010, 0.006),
        "treasury_10y_yield": (0.04, 0.08, 0.006, 0.02, 0.008, 0.005),
        "gdp_growth": (0.025, 0.15, 0.010, 0.02, -0.012, 0.007),
        "dollar_index_dxy": (100.0, 0.05, 0.8, 0.03, 0.0, 2.0),
        "manufacturing_pmi": (50.0, 0.20, 1.2, 0.02, -2.5, 1.5),
    }
    for name, (mu, theta, sigma, lam, jmu, jsig) in specs.items():
        x = np.empty(n, dtype=np.float64)
        x[0] = mu
        for t in range(1, n):
            drift = theta * (mu - x[t - 1]) * _DT
            diff = sigma * np.sqrt(_DT) * rng.standard_normal()
            jump = (jmu + jsig * rng.standard_normal()) if rng.random() < lam * _DT else 0.0
            x[t] = x[t - 1] + drift + diff + jump
        out[name] = x
    return out


def calibrate(input_path: str, output_path: str) -> dict[str, dict[str, float]]:
    panel = _load_panel(Path(input_path))
    coeffs: dict[str, dict[str, float]] = {}
    for column, feature in _SERIES_MAP.items():
        series = panel.get(column)
        if series is None or np.isfinite(series).sum() < 8:
            logger.info("skip %-20s (series '%s' absent/too short)", feature, column)
            continue
        coeffs[feature] = _calibrate_feature(feature, series)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"dt": _DT, "features": coeffs}, indent=2))
    logger.info("wrote %d calibrated features -> %s", len(coeffs), out)
    return coeffs


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="calibrate_world.py", description="ECONITH world calibrator")
    p.add_argument("--input", default="data/raw/macro_history.parquet")
    p.add_argument("--output", default="models/world/stochastic_coeffs.json")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    coeffs = calibrate(args.input, args.output)
    if not coeffs:
        logger.error("no features calibrated -- check the input panel columns")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
