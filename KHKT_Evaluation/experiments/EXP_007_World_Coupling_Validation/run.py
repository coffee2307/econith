# -*- coding: utf-8 -*-
"""EXP_007: World OFF vs ON (no shock) vs ON (scenario shocks)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai.simulator_engine.macro_vectors import default_world
from KHKT_Evaluation.common import data_loader, io_utils, metrics, paths, plotting, world_bridge

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
PLOTS = RESULTS / "plots"


def _equity(rets, pos, start=100_000.0):
    r = np.nan_to_num(rets, nan=0.0)
    p = np.nan_to_num(pos, nan=0.0)
    g = np.clip(1.0 + p * r, 1e-6, None)
    return start * np.cumprod(g)


def _risk(name, equity, rets, pos, dd_thr):
    dd = metrics.max_drawdown(equity)
    peak = np.maximum.accumulate(equity)
    dd_series = (peak - equity) / np.maximum(peak, 1e-12)
    crossings = int(np.sum((dd_series[1:] >= dd_thr) & (dd_series[:-1] < dd_thr)))
    active = np.isfinite(rets) & np.isfinite(pos)
    port = np.nan_to_num(pos, nan=0.0) * np.nan_to_num(rets, nan=0.0)
    vol = float(np.std(port[active])) if np.any(active) else float("nan")
    return {
        "case": name,
        "max_drawdown": dd,
        "portfolio_vol": vol,
        "volatility_change_vs_off": None,
        "sentinel_trigger_proxy": crossings,
        "n": int(np.sum(active)),
    }


def _apply_coupling(rets, pos, shock: dict):
    vol_m = float(shock["volatility_multiplier"])
    obi_s = float(shock["order_flow_shock"])
    rets_on = rets * vol_m + (obi_s * 1e-4)
    pos_on = np.clip(pos + obi_s, -1.0, 1.0)
    return rets_on, pos_on, vol_m, obi_s


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_007", paths.LOGS / "EXP_007.log")
    RESULTS.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(
            max_rows=int(cfg.get("max_rows", 25000)),
            stride=int(cfg.get("stride", 5)),
        )
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_007", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    rets = data_loader.price_log_returns(df["price"].to_numpy())
    sig = data_loader.position_signal_from_panel(df, rets)
    pos = sig["positions"]
    signal_meta = {
        "obi_column": sig["obi_column"],
        "signal_source": sig["signal_source"],
        "used_real_obi": bool(sig["used_real_obi"]),
    }
    dd_thr = float(cfg.get("dd_freeze_threshold", 0.03))

    # --- World OFF: no coupling (M_t=1, O_t=0 by definition) ---
    eq_off = _equity(rets, pos)
    risk_off = _risk("World_OFF", eq_off, rets, pos, dd_thr)
    shock_off = {
        "volatility_multiplier": 1.0,
        "order_flow_shock": 0.0,
        "liquidity_drain": 0.0,
        "spread_widening_bps": 0.0,
        "headline": "World OFF — no macro_to_micro coupling",
    }

    # --- World ON, no scenario shock: standing coupling from default_world ---
    world_base = default_world()
    shock_on0 = world_bridge.shock_vector(world_base)
    rets_on0, pos_on0, m_on0, o_on0 = _apply_coupling(rets, pos, shock_on0)
    eq_on0 = _equity(rets_on0, pos_on0)
    risk_on0 = _risk("World_ON_no_shock", eq_on0, rets_on0, pos_on0, dd_thr)
    risk_on0["volatility_change_vs_off"] = float(risk_on0["portfolio_vol"] - risk_off["portfolio_vol"])
    on0_row = {
        "case": "World_ON_no_shock",
        "vol_multiplier_Mt": m_on0,
        "order_flow_shock_Ot": o_on0,
        "Mt_delta_vs_off": m_on0 - 1.0,
        "Ot_delta_vs_off": o_on0 - 0.0,
        "off_portfolio_vol": risk_off["portfolio_vol"],
        "on_portfolio_vol": risk_on0["portfolio_vol"],
        "vol_delta_vs_off": risk_on0["volatility_change_vs_off"],
        "measurable_vs_off": bool(
            abs(risk_on0["volatility_change_vs_off"]) > 1e-12
            or abs(risk_on0["max_drawdown"] - risk_off["max_drawdown"]) > 1e-12
            or risk_on0["sentinel_trigger_proxy"] != risk_off["sentinel_trigger_proxy"]
            or abs(m_on0 - 1.0) > 1e-12
            or abs(o_on0) > 1e-12
        ),
        "shock_headline": shock_on0["headline"],
    }

    severity = float(cfg.get("severity", 0.7))
    scenarios = list(cfg.get("scenarios") or ["interest_rate", "inflation", "trade"])

    scenario_rows = []
    plot_labels = ["OFF_vol", "ON_no_shock_vol"]
    plot_vals = [risk_off["portfolio_vol"], risk_on0["portfolio_vol"]]
    for kind in scenarios:
        world = default_world()
        world = world_bridge.apply_scenario_shock(world, kind=kind, severity=severity)
        shock = world_bridge.shock_vector(world)
        rets_on, pos_on, vol_m, obi_s = _apply_coupling(rets, pos, shock)
        eq_on = _equity(rets_on, pos_on)
        risk_on = _risk(f"World_ON_{kind}", eq_on, rets_on, pos_on, dd_thr)
        risk_on["volatility_change_vs_off"] = float(risk_on["portfolio_vol"] - risk_off["portfolio_vol"])
        mt_delta_vs_on0 = vol_m - m_on0
        ot_delta_vs_on0 = obi_s - o_on0
        row = {
            "scenario": kind,
            "severity": severity,
            "vol_multiplier_Mt": vol_m,
            "order_flow_shock_Ot": obi_s,
            "Mt_delta_vs_off": vol_m - 1.0,
            "Ot_delta_vs_off": obi_s - 0.0,
            "Mt_delta_vs_on_no_shock": mt_delta_vs_on0,
            "Ot_delta_vs_on_no_shock": ot_delta_vs_on0,
            "shock_changes_Mt_or_Ot_vs_on_no_shock": bool(
                abs(mt_delta_vs_on0) > 1e-12 or abs(ot_delta_vs_on0) > 1e-12
            ),
            "off_portfolio_vol": risk_off["portfolio_vol"],
            "on_no_shock_portfolio_vol": risk_on0["portfolio_vol"],
            "on_portfolio_vol": risk_on["portfolio_vol"],
            "vol_delta_vs_off": risk_on["volatility_change_vs_off"],
            "vol_delta_vs_on_no_shock": float(risk_on["portfolio_vol"] - risk_on0["portfolio_vol"]),
            "off_max_drawdown": risk_off["max_drawdown"],
            "on_max_drawdown": risk_on["max_drawdown"],
            "dd_delta": risk_on["max_drawdown"] - risk_off["max_drawdown"],
            "off_sentinel_proxy": risk_off["sentinel_trigger_proxy"],
            "on_sentinel_proxy": risk_on["sentinel_trigger_proxy"],
            "sentinel_delta": risk_on["sentinel_trigger_proxy"] - risk_off["sentinel_trigger_proxy"],
            "measurable_difference": bool(
                abs(risk_on["volatility_change_vs_off"]) > 1e-12
                or abs(risk_on["max_drawdown"] - risk_off["max_drawdown"]) > 1e-12
                or risk_on["sentinel_trigger_proxy"] != risk_off["sentinel_trigger_proxy"]
            ),
            "shock_headline": shock["headline"],
        }
        scenario_rows.append(row)
        plot_labels.append(f"ON_{kind}_vol")
        plot_vals.append(risk_on["portfolio_vol"])
        logger.info(
            "%s measurable=%s Mt=%.4f Ot=%.4f dMt_vs_on0=%.6g dOt_vs_on0=%.6g",
            kind,
            row["measurable_difference"],
            vol_m,
            obi_s,
            mt_delta_vs_on0,
            ot_delta_vs_on0,
        )

    rate_row = next((r for r in scenario_rows if r["scenario"] == "interest_rate"), None)
    if sig["used_real_obi"]:
        signal_limit = (
            f"sign({sig['obi_column']}) signal is intentionally simple; "
            "absolute DD can be extreme — use deltas."
        )
    else:
        signal_limit = (
            "Positions used lagged return-sign fallback — NOT real OBI. "
            "Do not claim the experiment used OBI until indicator_obi/obi is present and re-run."
        )

    payload = {
        "experiment": "EXP_007",
        "status": "completed",
        "rq": "RQ3",
        "fabricated": False,
        "dataset": data_loader.dataset_provenance(df, path),
        "signal": signal_meta,
        "world_off": {**risk_off, "shock": shock_off},
        "world_on_no_shock": {**risk_on0, "shock": shock_on0, **on0_row},
        "scenarios": scenario_rows,
        "summary": {
            "scenarios_with_measurable_diff": sum(1 for r in scenario_rows if r["measurable_difference"]),
            "scenarios_total": len(scenario_rows),
            "on_no_shock_measurable_vs_off": on0_row["measurable_vs_off"],
            "interest_rate_changes_Mt_Ot_vs_on_no_shock": (
                None if rate_row is None else rate_row["shock_changes_Mt_or_Ot_vs_on_no_shock"]
            ),
            "Mt_Ot_notation": "Mt=volatility_multiplier, Ot=order_flow_shock from macro_to_micro",
        },
        "limitations": [
            "Offline shock application on returns (proxy), not live EventBus WorldKernel ticks.",
            signal_limit,
            "World_ON_no_shock uses default_world standing coupling (no apply_scenario_shock).",
            "Interest-rate path requires real_rate channel in macro_to_micro so Mt/Ot move under rate shocks.",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    csv_rows = [on0_row] + scenario_rows
    fieldnames = sorted({k for row in csv_rows for k in row.keys()})
    normalized_rows = [{k: row.get(k) for k in fieldnames} for row in csv_rows]
    io_utils.write_csv(RESULTS / "raw_results.csv", normalized_rows, fieldnames=fieldnames)
    io_utils.write_csv(RESULTS / "metrics.csv", normalized_rows, fieldnames=fieldnames)
    plotting.save_bar_chart(
        PLOTS / "world_on_off_vol.png",
        plot_labels,
        plot_vals,
        title="EXP_007 — Portfolio vol OFF / ON_no_shock / ON+scenario",
        ylabel="portfolio_vol",
    )

    lines = [
        "| Case | Mt | Ot | Vol | ΔVol vs OFF | ΔMt vs ON₀ | Shock moves Mt/Ot? |",
        "|---|---:|---:|---:|---:|---:|---|",
        f"| OFF | 1.0 | 0.0 | {risk_off['portfolio_vol']:.6g} | 0 | — | — |",
        f"| ON_no_shock | {m_on0:.4f} | {o_on0:.4f} | {risk_on0['portfolio_vol']:.6g} | "
        f"{on0_row['vol_delta_vs_off']:.6g} | 0 | — |",
    ]
    for r in scenario_rows:
        lines.append(
            f"| ON_{r['scenario']} | {r['vol_multiplier_Mt']:.4f} | {r['order_flow_shock_Ot']:.4f} | "
            f"{r['on_portfolio_vol']:.6g} | {r['vol_delta_vs_off']:.6g} | "
            f"{r['Mt_delta_vs_on_no_shock']:.6g} | {r['shock_changes_Mt_or_Ot_vs_on_no_shock']} |"
        )
    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_007 — World Coupling Validation (RQ3)

## Mục tiêu
Đo World OFF vs World ON (không sốc) vs World ON + scenario shocks (interest / inflation / trade).
`Mt` = volatility_multiplier, `Ot` = order_flow_shock.

## Kết quả
- Signal: `{signal_meta}`
- ON_no_shock measurable vs OFF: **{on0_row['measurable_vs_off']}**
- Measurable scenarios vs OFF: {payload['summary']['scenarios_with_measurable_diff']}/{payload['summary']['scenarios_total']}
- interest_rate changes Mt/Ot vs ON_no_shock: **{payload['summary']['interest_rate_changes_Mt_Ot_vs_on_no_shock']}**

{chr(10).join(lines)}

## Limitations
{chr(10).join('- ' + x for x in payload['limitations'])}
""",
    )
    return payload


if __name__ == "__main__":
    run()
