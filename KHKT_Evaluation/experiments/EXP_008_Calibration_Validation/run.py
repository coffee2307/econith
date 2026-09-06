# -*- coding: utf-8 -*-
"""EXP_008: Calibration validation — default vs calibrated vs real distributions."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from econith.world.sovereign.stochastic import FeatureProcess, _DEFAULT_PROCESSES
from KHKT_Evaluation.common import data_loader, io_utils, macro_series, metrics, paths, plotting
from KHKT_Evaluation.experiments.EXP_002_calibration import run as exp002

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
PLOTS = RESULTS / "plots"


def _simulate(proc: FeatureProcess, n: int, seed: int, dt: float) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = np.empty(n, dtype=np.float64)
    x[0] = proc.mu
    for t in range(1, n):
        drift = proc.theta * (proc.mu - x[t - 1]) * dt
        diff = proc.sigma * np.sqrt(dt) * rng.standard_normal()
        jump = 0.0
        if rng.random() < proc.jump_intensity * dt:
            jump = proc.jump_mean + proc.jump_std * rng.standard_normal()
        x[t] = float(np.clip(x[t - 1] + drift + diff + jump, proc.lo, proc.hi))
    return x


def _hist_overlay(path: Path, real, default_s, cal_s, title: str) -> bool:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.hist(real, bins=40, density=True, alpha=0.45, label="Real data", color="#333333")
    ax.hist(default_s, bins=40, density=True, alpha=0.35, label="Default sim", color="#A0A0A0")
    ax.hist(cal_s, bins=40, density=True, alpha=0.40, label="Calibrated sim", color="#1F4E79")
    ax.set_title(title)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return True


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_008", paths.LOGS / "EXP_008.log")
    RESULTS.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    # Reuse EXP_002 logic for moment table, then add distribution plots
    base = exp002.run()
    if base.get("status") != "completed":
        payload = {
            "experiment": "EXP_008",
            "status": base.get("status", "blocked"),
            "reason": base.get("reason"),
            "fabricated": False,
            "upstream": "EXP_002",
        }
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    # Build empirical + sims using shared macro_series (same units as EXP_002)
    try:
        df = data_loader.load_btc_panel(max_rows=50_000, stride=3)
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_008", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    empirical = macro_series.empirical_macro_from_df(df, collapse_duplicates=True)

    defaults = {p.name: p for p in _DEFAULT_PROCESSES}
    cal_path = paths.CALIBRATED_COEFFS
    cal = {}
    dt = 1.0 / 252.0
    if cal_path.exists():
        raw = json.loads(cal_path.read_text(encoding="utf-8"))
        dt = float(raw.get("dt", dt))
        for name, c in (raw.get("features") or {}).items():
            cal[name] = FeatureProcess(
                name=name,
                theta=float(c["theta"]),
                mu=float(c["mu"]),
                sigma=float(c["sigma"]),
                jump_intensity=float(c["jump_intensity"]),
                jump_mean=float(c["jump_mean"]),
                jump_std=float(c["jump_std"]),
                lo=float(c["lo"]),
                hi=float(c["hi"]),
            )

    n_steps = int(cfg.get("n_steps", 2520))
    n_paths = int(cfg.get("n_paths", 12))
    seed = int(cfg.get("seed", 7))
    dist_rows = []
    for feat in ("interest_rate", "yield_10y", "gdp_growth", "inflation_cpi"):
        dproc, cproc = defaults.get(feat), cal.get(feat)
        emp = empirical.get(feat)
        if dproc is None or cproc is None or emp is None or emp.size < 8:
            continue
        sim_d = np.concatenate([_simulate(dproc, n_steps, seed + i, dt) for i in range(n_paths)])
        sim_c = np.concatenate([_simulate(cproc, n_steps, seed + i, dt) for i in range(n_paths)])
        err_d = metrics.moment_errors(emp, sim_d)
        err_c = metrics.moment_errors(emp, sim_c)
        dist_rows.append(
            {
                "feature": feat,
                "default_moment_l1": err_d["moment_l1"],
                "calibrated_moment_l1": err_c["moment_l1"],
                "default_abs_mean_error": err_d["abs_mean_error"],
                "calibrated_abs_mean_error": err_c["abs_mean_error"],
                "default_abs_variance_error": err_d["abs_variance_error"],
                "calibrated_abs_variance_error": err_c["abs_variance_error"],
                "calibrated_better": bool(err_c["moment_l1"] < err_d["moment_l1"]),
            }
        )
        _hist_overlay(
            PLOTS / f"dist_{feat}.png",
            emp,
            sim_d,
            sim_c,
            title=f"EXP_008 — {feat}: Real vs Default vs Calibrated",
        )
        logger.info("%s cal_better=%s", feat, dist_rows[-1]["calibrated_better"])
    # also copy moment_l1 style chart
    if dist_rows:
        labels, vals = [], []
        for r in dist_rows:
            labels += [f"{r['feature']}_def", f"{r['feature']}_cal"]
            vals += [float(r["default_moment_l1"]), float(r["calibrated_moment_l1"])]
        plotting.save_bar_chart(
            PLOTS / "moment_l1.png",
            labels,
            vals,
            title="EXP_008 — Moment L1 (lower closer to real)",
            ylabel="moment_l1",
        )

    payload = {
        "experiment": "EXP_008",
        "status": "completed",
        "rq": "RQ2",
        "fabricated": False,
        "upstream_EXP_002_summary": base.get("summary"),
        "distribution_comparisons": dist_rows,
        "summary": {
            "features_plotted": len(dist_rows),
            "calibrated_wins": sum(1 for r in dist_rows if r["calibrated_better"]),
        },
        "limitations": [
            "Single-feature OU+jump paths — not full multi-agent World dynamics.",
            "Distribution distance reported via moment L1 (mean/std/skew), not full KS/Wasserstein (optional TODO).",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    io_utils.write_csv(RESULTS / "raw_results.csv", dist_rows)
    io_utils.write_csv(RESULTS / "metrics.csv", dist_rows)
    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_008 — Calibration Validation (RQ2)

## Mục tiêu
So sánh phân phối / moment: **Real data** vs **Default sim** vs **Calibrated sim**.

## Kết quả
- Features plotted: {payload['summary']['features_plotted']}
- Calibrated wins (lower moment L1): {payload['summary']['calibrated_wins']}

Biểu đồ: `results/plots/dist_*.png` (Before/After/Real overlay).

## Limitations
{chr(10).join('- ' + x for x in payload['limitations'])}
""",
    )
    return payload


if __name__ == "__main__":
    run()
