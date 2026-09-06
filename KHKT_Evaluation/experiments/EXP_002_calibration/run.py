# -*- coding: utf-8 -*-
"""EXP_002: Default vs calibrated stochastic params — moment distance to empirical macro."""
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

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"


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


def _default_map() -> dict[str, FeatureProcess]:
    return {p.name: p for p in _DEFAULT_PROCESSES}


def _calibrated_map(path: Path) -> tuple[dict[str, FeatureProcess], float, str]:
    if not path.exists():
        return {}, 1.0 / 252.0, "missing_coeffs"
    data = json.loads(path.read_text(encoding="utf-8"))
    dt = float(data.get("dt", 1.0 / 252.0))
    feats = data.get("features") or {}
    out: dict[str, FeatureProcess] = {}
    for name, c in feats.items():
        out[name] = FeatureProcess(
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
    return out, dt, "loaded"

def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_002", paths.LOGS / "EXP_002.log")
    RESULTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(max_rows=50_000, stride=3)
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_002", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    empirical = macro_series.empirical_macro_from_df(df, collapse_duplicates=True)
    defaults = _default_map()
    cal_path = _ROOT / cfg.get("calibrated_coeffs", "models/world/stochastic_coeffs.json")
    calibrated, dt, cal_status = _calibrated_map(cal_path)

    if not empirical:
        payload = {
            "experiment": "EXP_002",
            "status": "blocked",
            "reason": "no empirical macro series with enough points",
            "fabricated": False,
            "todo": ["Ensure BTC features contain macro_* columns with real values"],
        }
        io_utils.write_json(RESULTS / "metrics.json", payload)
        io_utils.write_markdown(EXP_DIR / "report.md", "# EXP_002 BLOCKED\n")
        return payload

    n_steps = int(cfg.get("n_steps", 2520))
    n_paths = int(cfg.get("n_paths", 8))
    seed = int(cfg.get("seed", 7))
    rows = []
    per_feature = {}

    for feat, emp in empirical.items():
        dproc = defaults.get(feat)
        cproc = calibrated.get(feat)
        entry = {"feature": feat, "empirical_n": int(emp.size), "has_default": dproc is not None, "has_calibrated": cproc is not None}
        if dproc is None and cproc is None:
            entry["status"] = "skipped"
            per_feature[feat] = entry
            continue

        # Same RNG seeds for default vs calibrated so moment L1 compares parameters,
        # not lucky seed offsets (RQ2 fairness).
        def pool(proc: FeatureProcess, tag: str) -> np.ndarray:
            del tag  # kept for call-site compatibility
            chunks = []
            for p in range(n_paths):
                chunks.append(_simulate(proc, n_steps, seed + p * 17, dt))
            return np.concatenate(chunks)

        if dproc is not None:
            sim_def = pool(dproc, "def")
            err_def = metrics.moment_errors(emp, sim_def)
        else:
            err_def = None
        if cproc is not None:
            sim_cal = pool(cproc, "cal")
            err_cal = metrics.moment_errors(emp, sim_cal)
        else:
            err_cal = None

        entry.update(
            {
                "status": "ok",
                "default_moment_l1": None if err_def is None else err_def["moment_l1"],
                "calibrated_moment_l1": None if err_cal is None else err_cal["moment_l1"],
                "default_errors": err_def,
                "calibrated_errors": err_cal,
                "calibrated_better_l1": (
                    None
                    if err_def is None or err_cal is None
                    else bool(err_cal["moment_l1"] < err_def["moment_l1"])
                ),
            }
        )
        per_feature[feat] = entry
        rows.append(
            {
                "feature": feat,
                "empirical_n": entry["empirical_n"],
                "default_moment_l1": entry["default_moment_l1"],
                "calibrated_moment_l1": entry["calibrated_moment_l1"],
                "calibrated_better_l1": entry["calibrated_better_l1"],
                "default_abs_mean_error": None if err_def is None else err_def["abs_mean_error"],
                "calibrated_abs_mean_error": None if err_cal is None else err_cal["abs_mean_error"],
                "default_abs_variance_error": None if err_def is None else err_def["abs_variance_error"],
                "calibrated_abs_variance_error": None if err_cal is None else err_cal["abs_variance_error"],
            }
        )
        logger.info(
            "%s default_L1=%s cal_L1=%s better=%s",
            feat,
            entry["default_moment_l1"],
            entry["calibrated_moment_l1"],
            entry["calibrated_better_l1"],
        )

    compared = [r for r in rows if r["calibrated_better_l1"] is not None]
    wins = sum(1 for r in compared if r["calibrated_better_l1"])

    payload = {
        "experiment": "EXP_002",
        "status": "completed",
        "rq": "RQ2",
        "fabricated": False,
        "calibration_file": str(cal_path),
        "calibration_status": cal_status,
        "dataset": data_loader.dataset_provenance(df, path),
        "dt_calibrated": dt,
        "n_steps": n_steps,
        "n_paths": n_paths,
        "features": per_feature,
        "summary": {
            "features_compared": len(compared),
            "calibrated_wins_moment_l1": wins,
            "default_wins_moment_l1": len(compared) - wins,
        },
        "notes": [
            "Moment L1 = |Δmean| + |Δstd| + |Δskew| between simulated paths and empirical series.",
            "Lower moment_l1 is better (closer to empirical).",
            "Empirical series from shared macro_series helper (WB CPI YoY preferred; collapsed asof duplicates).",
            "Default and calibrated simulations use the same dt from stochastic_coeffs.json.",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    io_utils.write_csv(RESULTS / "metrics.csv", rows)

    labels, vals = [], []
    for r in rows:
        if r["default_moment_l1"] is not None:
            labels.append(f"{r['feature']}_def")
            vals.append(float(r["default_moment_l1"]))
        if r["calibrated_moment_l1"] is not None:
            labels.append(f"{r['feature']}_cal")
            vals.append(float(r["calibrated_moment_l1"]))
    if labels:
        plotting.save_bar_chart(
            RESULTS / "moment_l1.png",
            labels,
            vals,
            title="EXP_002 — Moment L1 (lower = closer to empirical)",
            ylabel="moment_l1",
        )

    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_002 — Calibration evaluation (RQ2)

## Mục tiêu
So sánh tham số **default** vs **calibrated** (`stochastic_coeffs.json`) theo khoảng cách moment với chuỗi macro thực.

## Trạng thái
**{payload['status']}** — calibration_status={cal_status}

## Kết quả tóm tắt
- Features compared: {payload['summary']['features_compared']}
- Calibrated wins (lower moment L1): {payload['summary']['calibrated_wins_moment_l1']}
- Default wins: {payload['summary']['default_wins_moment_l1']}

Chi tiết: `results/metrics.csv`, `results/metrics.json`.

## Giới hạn
- Mô phỏng OU+jump đơn biến, không phải full World multi-agent.
- Empirical inflation ưu tiên `macro_wb_inflation_cpi_pct` (YoY), không dùng diff index thô.
""",
    )
    return payload


if __name__ == "__main__":
    run()
