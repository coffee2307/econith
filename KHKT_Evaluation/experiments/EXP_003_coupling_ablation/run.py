# -*- coding: utf-8 -*-
"""EXP_003: Same market path — coupling OFF vs ON_no_shock vs ON+rate_hike."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import data_loader, io_utils, metrics, paths, plotting, world_bridge

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"


def _equity_from_returns(rets: np.ndarray, pos: np.ndarray, start: float = 100_000.0) -> np.ndarray:
    r = np.nan_to_num(rets, nan=0.0)
    p = np.nan_to_num(pos, nan=0.0)
    growth = 1.0 + p * r
    growth = np.clip(growth, 1e-6, None)
    return start * np.cumprod(growth)


def _risk_block(name: str, equity: np.ndarray, rets: np.ndarray, pos: np.ndarray, dd_thr: float) -> dict:
    eq = equity
    dd = metrics.max_drawdown(eq)
    peak = np.maximum.accumulate(eq)
    dd_series = (peak - eq) / np.maximum(peak, 1e-12)
    crossings = int(np.sum((dd_series[1:] >= dd_thr) & (dd_series[:-1] < dd_thr)))
    active = np.isfinite(rets) & np.isfinite(pos)
    port_rets = np.nan_to_num(pos, nan=0.0) * np.nan_to_num(rets, nan=0.0)
    vol = float(np.std(port_rets[active])) if np.any(active) else float("nan")
    return {
        "case": name,
        "max_drawdown": dd,
        "portfolio_vol": vol,
        "sentinel_freeze_proxy_count": crossings,
        "end_equity": float(eq[-1]) if eq.size else float("nan"),
        "n": int(np.sum(active)),
    }


def _apply(rets, pos, shock):
    vol_m = float(shock["volatility_multiplier"])
    obi_s = float(shock["order_flow_shock"])
    return rets * vol_m + (obi_s * 1e-4), np.clip(pos + obi_s, -1.0, 1.0), vol_m, obi_s


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_003", paths.LOGS / "EXP_003.log")
    RESULTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(max_rows=int(cfg.get("max_rows", 30000)), stride=int(cfg.get("stride", 5)))
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_003", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    rets = data_loader.price_log_returns(df["price"].to_numpy())
    sig = data_loader.position_signal_from_panel(df, rets)
    pos = sig["positions"]
    dd_thr = float(cfg.get("dd_freeze_threshold", 0.03))

    # A: coupling OFF
    eq_a = _equity_from_returns(rets, pos)
    risk_a = _risk_block("A_coupling_OFF", eq_a, rets, pos, dd_thr)

    from ai.simulator_engine.macro_vectors import default_world

    # B: World ON, no scenario shock
    world_b = default_world()
    shock_b = world_bridge.shock_vector(world_b)
    rets_b, pos_b, m_b, o_b = _apply(rets, pos, shock_b)
    eq_b = _equity_from_returns(rets_b, pos_b)
    risk_b = _risk_block("B_ON_no_shock", eq_b, rets_b, pos_b, dd_thr)

    # C: World ON + rate hike
    world_c = default_world()
    world_c = world_bridge.apply_scenario_shock(
        world_c,
        kind=str(cfg.get("scenario_kind", "rate_hike")),
        severity=float(cfg.get("severity", 0.7)),
    )
    shock_c = world_bridge.shock_vector(world_c)
    rets_c, pos_c, m_c, o_c = _apply(rets, pos, shock_c)
    eq_c = _equity_from_returns(rets_c, pos_c)
    risk_c = _risk_block("C_ON_rate_hike", eq_c, rets_c, pos_c, dd_thr)

    delta_b_a = {
        "portfolio_vol_delta": risk_b["portfolio_vol"] - risk_a["portfolio_vol"],
        "max_drawdown_delta": risk_b["max_drawdown"] - risk_a["max_drawdown"],
        "sentinel_proxy_delta": risk_b["sentinel_freeze_proxy_count"] - risk_a["sentinel_freeze_proxy_count"],
        "Mt_delta": m_b - 1.0,
        "Ot_delta": o_b - 0.0,
        "measurable_difference": bool(
            abs(risk_b["portfolio_vol"] - risk_a["portfolio_vol"]) > 1e-12
            or abs(m_b - 1.0) > 1e-12
            or abs(o_b) > 1e-12
        ),
    }
    delta_c_b = {
        "portfolio_vol_delta": risk_c["portfolio_vol"] - risk_b["portfolio_vol"],
        "max_drawdown_delta": risk_c["max_drawdown"] - risk_b["max_drawdown"],
        "sentinel_proxy_delta": risk_c["sentinel_freeze_proxy_count"] - risk_b["sentinel_freeze_proxy_count"],
        "Mt_delta": m_c - m_b,
        "Ot_delta": o_c - o_b,
        "shock_changes_Mt_or_Ot": bool(abs(m_c - m_b) > 1e-12 or abs(o_c - o_b) > 1e-12),
        "measurable_difference": bool(
            abs(risk_c["portfolio_vol"] - risk_b["portfolio_vol"]) > 1e-12
            or abs(m_c - m_b) > 1e-12
            or abs(o_c - o_b) > 1e-12
        ),
    }
    # Keep legacy delta key for summary compatibility (C vs A)
    delta = {
        "max_drawdown_delta": risk_c["max_drawdown"] - risk_a["max_drawdown"],
        "portfolio_vol_delta": risk_c["portfolio_vol"] - risk_a["portfolio_vol"],
        "sentinel_proxy_delta": risk_c["sentinel_freeze_proxy_count"] - risk_a["sentinel_freeze_proxy_count"],
        "measurable_difference": bool(
            abs(risk_c["max_drawdown"] - risk_a["max_drawdown"]) > 1e-8
            or abs(risk_c["portfolio_vol"] - risk_a["portfolio_vol"]) > 1e-12
            or risk_c["sentinel_freeze_proxy_count"] != risk_a["sentinel_freeze_proxy_count"]
        ),
    }

    signal_meta = {
        "obi_column": sig["obi_column"],
        "signal_source": sig["signal_source"],
        "used_real_obi": bool(sig["used_real_obi"]),
    }
    if sig["used_real_obi"]:
        signal_note = f"Positions use sign({sig['obi_column']})."
    else:
        signal_note = "Lagged return-sign fallback — NOT real OBI."

    payload = {
        "experiment": "EXP_003",
        "status": "completed",
        "rq": "RQ3",
        "fabricated": False,
        "dataset": data_loader.dataset_provenance(df, path),
        "signal": signal_meta,
        "scenario": {
            "kind": cfg.get("scenario_kind"),
            "severity": cfg.get("severity"),
            "shock_on_no_shock": shock_b,
            "shock_on_rate": shock_c,
        },
        "case_A": {**risk_a, "Mt": 1.0, "Ot": 0.0},
        "case_B": {**risk_b, "Mt": m_b, "Ot": o_b},
        "case_C": {**risk_c, "Mt": m_c, "Ot": o_c},
        "delta_B_vs_A_on_no_shock": delta_b_a,
        "delta_C_vs_B_rate_shock": delta_c_b,
        "delta": delta,
        "notes": [
            signal_note,
            "A=World OFF; B=World ON no scenario shock; C=World ON + rate_hike.",
            "Rate shock must change Mt/Ot vs B (real_rate channel in macro_to_micro).",
            "sentinel_freeze_proxy_count counts drawdown threshold crossings (not live Sentinel).",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    io_utils.write_csv(RESULTS / "metrics.csv", [risk_a, risk_b, risk_c])
    plotting.save_line_chart(
        RESULTS / "equity_curves.png",
        {"A_OFF": eq_a, "B_ON0": eq_b, "C_rate": eq_c},
        title="EXP_003 — Equity OFF / ON_no_shock / ON+rate",
        ylabel="equity",
    )
    plotting.save_bar_chart(
        RESULTS / "risk_delta.png",
        ["vol_A", "vol_B", "vol_C", "Mt_B", "Mt_C"],
        [risk_a["portfolio_vol"], risk_b["portfolio_vol"], risk_c["portfolio_vol"], m_b, m_c],
        title="EXP_003 — Vol and Mt ablation",
        ylabel="value",
    )
    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_003 — Coupling ablation (RQ3)

## Mục tiêu
Cùng path: OFF vs ON (không sốc) vs ON+rate_hike. Mt/Ot phải đổi khi có rate shock.

## Kết quả
- Signal: `{signal_meta}`
- B vs A (ON_no_shock): measurable={delta_b_a['measurable_difference']}, Δvol={delta_b_a['portfolio_vol_delta']:.6g}, Mt={m_b:.4f}, Ot={o_b:.4f}
- C vs B (rate shock): measurable={delta_c_b['measurable_difference']}, shock_changes_Mt_Ot={delta_c_b['shock_changes_Mt_or_Ot']}, ΔMt={delta_c_b['Mt_delta']:.6g}, ΔOt={delta_c_b['Ot_delta']:.6g}
- C vs A (legacy): measurable={delta['measurable_difference']}, Δvol={delta['portfolio_vol_delta']:.6g}

## Giới hạn
Offline proxy của coupling (không phải full EventBus SIMULATION runtime).
""",
    )
    logger.info(
        "B_vs_A=%s C_vs_B_MtOt=%s",
        delta_b_a["measurable_difference"],
        delta_c_b["shock_changes_Mt_or_Ot"],
    )
    return payload


if __name__ == "__main__":
    run()
