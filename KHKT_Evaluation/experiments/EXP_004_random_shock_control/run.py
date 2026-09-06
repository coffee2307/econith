# -*- coding: utf-8 -*-
"""EXP_004: Structured World shock vs random vol multipliers (control)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import data_loader, io_utils, paths, plotting, vol_eval

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
PLOTS = RESULTS / "plots"


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_004", paths.LOGS / "EXP_004.log")
    RESULTS.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(
            max_rows=int(cfg.get("max_rows", 30000)),
            stride=int(cfg.get("stride", 5)),
        )
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_004", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    ev = vol_eval.evaluate_b0_e1_c1(
        df,
        hist_vol_window=int(cfg.get("hist_vol_window", 60)),
        fwd_vol_horizon=int(cfg.get("fwd_vol_horizon", 60)),
        macro_sample_every=int(cfg.get("macro_sample_every", 10)),
        seed=int(cfg.get("seed", 123)),
        protocol=str(cfg.get("protocol", "oos_beta")),
        train_frac=float(cfg.get("train_frac", 0.70)),
    )
    m = ev["metrics"]
    payload = {
        "experiment": "EXP_004",
        "status": "completed",
        "rq": "RQ1_control",
        "fabricated": False,
        "dataset": data_loader.dataset_provenance(df, path),
        "protocol": ev.get("protocol"),
        "beta": ev.get("beta"),
        "lag": ev.get("lag"),
        "fingerprint": ev.get("fingerprint"),
        "metrics": m,
        "comparison": {
            "e1_better_mae_than_b0": ev["comparison"]["e1_better_mae_than_b0"],
            "e1_better_mae_than_c1": ev["comparison"]["e1_better_mae_than_c1"],
            "e1_better_rmse_than_c1": (
                bool(m["E1"]["rmse"] < m["C1"]["rmse"])
                if m["E1"]["rmse"] == m["E1"]["rmse"] and m["C1"]["rmse"] == m["C1"]["rmse"]
                else None
            ),
            "passes_random_control": ev["comparison"]["passes_random_control"],
        },
        "aux": ev.get("aux"),
        "protocol_legacy": ev.get("protocol_legacy"),
        "vol_multiplier_stats": ev["vol_multiplier_stats"],
        "notes": [
            "C1 shuffles lagged (vol_mult-1) after beta is fit on train (fair control).",
            "If E1 is not better than C1, structured World timing is not helping beyond noise.",
            "protocol_legacy retains multiplicative baseline for historical contrast.",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    rows = [
        {"model": k, **{f: m[k][f] for f in ("n", "mae", "rmse", "directional_accuracy")}}
        for k in ("B0", "E1", "C1")
    ]
    io_utils.write_csv(RESULTS / "metrics.csv", rows)
    io_utils.write_csv(RESULTS / "raw_results.csv", rows)
    plotting.save_bar_chart(
        PLOTS / "control_mae.png",
        ["B0", "E1", "C1"],
        [m["B0"]["mae"], m["E1"]["mae"], m["C1"]["mae"]],
        title="EXP_004 — MAE vs random control (lower better)",
        ylabel="MAE",
    )
    plotting.save_bar_chart(
        RESULTS / "control_mae.png",
        ["B0", "E1", "C1"],
        [m["B0"]["mae"], m["E1"]["mae"], m["C1"]["mae"]],
        title="EXP_004 — MAE vs random control (lower better)",
        ylabel="MAE",
    )
    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_004 — Random shock control

## Mục tiêu
Kiểm tra E1 (World structured) có tốt hơn nhiễu ngẫu nhiên (C1) trên MAE/RMSE không.

## Kết quả
- E1 better than B0 (MAE): {payload['comparison']['e1_better_mae_than_b0']}
- E1 better than C1 (MAE): {payload['comparison']['e1_better_mae_than_c1']}
- Passes random control: **{payload['comparison']['passes_random_control']}**
- vol_mult unique≈{ev['vol_multiplier_stats']['unique_approx']}

| Model | MAE | RMSE | DirAcc |
|---|---:|---:|---:|
| B0 | {m['B0']['mae']:.6g} | {m['B0']['rmse']:.6g} | {m['B0']['directional_accuracy']:.4f} |
| E1 | {m['E1']['mae']:.6g} | {m['E1']['rmse']:.6g} | {m['E1']['directional_accuracy']:.4f} |
| C1 | {m['C1']['mae']:.6g} | {m['C1']['rmse']:.6g} | {m['C1']['directional_accuracy']:.4f} |
""",
    )
    logger.info("passes_random_control=%s", payload["comparison"]["passes_random_control"])
    return payload


if __name__ == "__main__":
    run()
