# -*- coding: utf-8 -*-
"""Bridge World state ↔ macro_to_micro for offline experiments."""
from __future__ import annotations

from typing import Any

import numpy as np

from ai.simulator_engine.cross_impact import macro_to_micro
from ai.simulator_engine.macro_vectors import WorldState, default_world


def _pct_to_frac(x: float) -> float:
    return float(x) / 100.0 if abs(x) > 1.0 else float(x)


def world_from_macro_row(
    *,
    fed_funds_pct: float | None = None,
    cpi_index: float | None = None,
    treasury_10y_pct: float | None = None,
    gdp_growth: float | None = None,
    unemployment: float | None = None,
    vix: float | None = None,
    yield_spread_10y_2y: float | None = None,
    hy_oas: float | None = None,
) -> WorldState:
    """Clone default world and overlay USA macro from feature-store columns.

    High-frequency stress proxies (VIX, HY OAS, curve spread) are mapped into
    geopolitical / unrest / growth fields so ``macro_to_micro`` produces
    *time-varying* volatility multipliers (required for RQ1 experiments).
    """
    world = default_world()
    usa = world.countries.get("USA")
    if usa is None:
        return world

    if fed_funds_pct is not None and np.isfinite(fed_funds_pct):
        rate = _pct_to_frac(float(fed_funds_pct))
        usa.monetary.interest_rate = float(np.clip(rate, 0.0, 0.25))

    if treasury_10y_pct is not None and np.isfinite(treasury_10y_pct):
        y = _pct_to_frac(float(treasury_10y_pct))
        usa.monetary.yield_10y = float(np.clip(y, -0.02, 0.35))

    if gdp_growth is not None and np.isfinite(gdp_growth):
        g = _pct_to_frac(float(gdp_growth))
        usa.gdp_growth = float(np.clip(g, -0.20, 0.20))

    if unemployment is not None and np.isfinite(unemployment):
        u = _pct_to_frac(float(unemployment))
        usa.labor.unemployment = float(np.clip(u, 0.005, 0.45))

    # --- time-varying stress proxies (critical for RQ1) ---
    if vix is not None and np.isfinite(vix):
        # Map VIX≈12..80 → risk/unrest in [0,1]
        risk = float(np.clip((float(vix) - 12.0) / 50.0, 0.0, 1.0))
        usa.geopolitical.geopolitical_risk = float(np.clip(0.15 + 0.75 * risk, 0.0, 1.0))
        usa.geopolitical.social_unrest_index = float(np.clip(0.10 + 0.55 * risk, 0.0, 1.0))
        usa.geopolitical.business_confidence = float(np.clip(0.75 - 0.45 * risk, 0.05, 0.98))

    if yield_spread_10y_2y is not None and np.isfinite(yield_spread_10y_2y):
        # Inversion (negative spread) → softer growth / higher stress
        spread = float(yield_spread_10y_2y)
        if spread < 0:
            usa.gdp_growth = float(np.clip(usa.gdp_growth - 0.01 * abs(spread), -0.20, 0.20))
            usa.geopolitical.geopolitical_risk = float(
                np.clip(usa.geopolitical.geopolitical_risk + 0.08 * abs(spread), 0.0, 1.0)
            )

    if hy_oas is not None and np.isfinite(hy_oas):
        # Credit spread stress (typical ~2.5–5+): map excess over 3.0
        credit = float(np.clip((float(hy_oas) - 3.0) / 3.0, 0.0, 1.0))
        usa.geopolitical.sanctions_exposure = float(np.clip(0.08 + 0.35 * credit, 0.0, 1.0))
        # Mild tariff tension proxy under credit stress
        base_t = world.tariff("USA", "CHN")
        world.set_tariff("USA", "CHN", float(np.clip(base_t + 0.05 * credit, 0.0, 1.0)))

    _ = cpi_index  # index levels are not YoY; do not invent inflation
    return world


def shock_vector(world: WorldState) -> dict[str, Any]:
    mv = macro_to_micro(world)
    return {
        "volatility_multiplier": float(mv.volatility_multiplier),
        "order_flow_shock": float(mv.order_flow_shock),
        "liquidity_drain": float(mv.liquidity_drain),
        "spread_widening_bps": float(mv.spread_widening_bps),
        "headline": mv.headline,
    }


def apply_scenario_shock(
    world: WorldState,
    *,
    kind: str,
    severity: float,
) -> WorldState:
    """Apply a structured scenario shock onto USA (and optionally CHN tariff)."""
    usa = world.countries["USA"]
    sev = float(np.clip(severity, 0.0, 1.0))
    if kind in {"rate_hike", "interest_rate"}:
        usa.monetary.interest_rate = float(
            np.clip(usa.monetary.interest_rate + 0.025 * sev, 0.0, 0.25)
        )
    elif kind in {"inflation_spike", "inflation"}:
        usa.monetary.inflation_cpi = float(
            np.clip(usa.monetary.inflation_cpi + 0.04 * sev, -0.03, 0.15)
        )
    elif kind in {"tariff", "trade"}:
        world.set_tariff(
            "USA",
            "CHN",
            float(np.clip(world.tariff("USA", "CHN") + 0.25 * sev, 0.0, 1.0)),
        )
        usa.geopolitical.geopolitical_risk = float(
            np.clip(usa.geopolitical.geopolitical_risk + 0.2 * sev, 0.0, 1.0)
        )
    elif kind == "growth_crash":
        usa.gdp_growth = float(np.clip(usa.gdp_growth - 0.06 * sev, -0.20, 0.20))
        usa.geopolitical.social_unrest_index = float(
            np.clip(usa.geopolitical.social_unrest_index + 0.15 * sev, 0.0, 1.0)
        )
    else:
        raise ValueError(f"unknown scenario kind: {kind}")
    return world


def vol_multipliers_for_panel(df, *, sample_every: int = 1) -> np.ndarray:
    """Compute per-row volatility_multiplier from macro columns via World bridge."""
    n = len(df)
    out = np.ones(n, dtype=np.float64)

    def col(name: str):
        return df[name].to_numpy() if name in df.columns else None

    fed = col("macro_fed_funds_effective_rate")
    y10 = col("macro_treasury_10y_yield")
    gdp = col("macro_real_gdp_growth")
    une = col("macro_unemployment_rate")
    vix = col("macro_vix")
    spr = col("macro_yield_spread_10y_2y")
    hy = col("macro_hy_oas_spread")

    cache: dict[tuple, float] = {}
    step = max(1, sample_every)
    for i in range(0, n, step):
        key = (
            None if fed is None or not np.isfinite(fed[i]) else round(float(fed[i]), 3),
            None if y10 is None or not np.isfinite(y10[i]) else round(float(y10[i]), 3),
            None if gdp is None or not np.isfinite(gdp[i]) else round(float(gdp[i]), 3),
            None if une is None or not np.isfinite(une[i]) else round(float(une[i]), 3),
            None if vix is None or not np.isfinite(vix[i]) else round(float(vix[i]), 2),
            None if spr is None or not np.isfinite(spr[i]) else round(float(spr[i]), 3),
            None if hy is None or not np.isfinite(hy[i]) else round(float(hy[i]), 3),
        )
        if key not in cache:
            w = world_from_macro_row(
                fed_funds_pct=key[0],
                treasury_10y_pct=key[1],
                gdp_growth=key[2],
                unemployment=key[3],
                vix=key[4],
                yield_spread_10y_2y=key[5],
                hy_oas=key[6],
            )
            cache[key] = float(shock_vector(w)["volatility_multiplier"])
        out[i] = cache[key]

    last = out[0] if n else 1.0
    for i in range(n):
        if i % step == 0:
            last = out[i]
        else:
            out[i] = last
    return out
