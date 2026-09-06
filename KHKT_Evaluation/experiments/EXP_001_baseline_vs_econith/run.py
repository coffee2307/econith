# -*- coding: utf-8 -*-
"""EXP_001: Baseline (market-only) vs ECONITH World-coupled vs random control."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import data_loader, io_utils, paths, plotting, vol_eval

EXP_DIR = Path(__file__).resolve().parent
RESULTS = EXP_DIR / "results"
PLOTS = RESULTS / "plots"


def run() -> dict:
    cfg = io_utils.load_yaml(EXP_DIR / "config.yaml")
    logger = io_utils.setup_logger("EXP_001", paths.LOGS / "EXP_001.log")
    RESULTS.mkdir(parents=True, exist_ok=True)
    PLOTS.mkdir(parents=True, exist_ok=True)

    try:
        path = data_loader.resolve_btc_features()
        df = data_loader.load_btc_panel(
            max_rows=int(cfg.get("max_rows", 30000)),
            stride=int(cfg.get("stride", 5)),
        )
    except FileNotFoundError as exc:
        payload = {"experiment": "EXP_001", "status": "blocked", "reason": str(exc), "fabricated": False}
        io_utils.write_json(RESULTS / "metrics.json", payload)
        return payload

    protocol = str(cfg.get("protocol", "oos_beta"))
    train_frac = float(cfg.get("train_frac", 0.70))
    ev = vol_eval.evaluate_b0_e1_c1(
        df,
        hist_vol_window=int(cfg.get("hist_vol_window", 60)),
        fwd_vol_horizon=int(cfg.get("fwd_vol_horizon", 60)),
        macro_sample_every=int(cfg.get("macro_sample_every", 10)),
        seed=int(cfg.get("seed", 42)),
        protocol=protocol,
        train_frac=train_frac,
    )
    m = ev["metrics"]
    payload = {
        "experiment": "EXP_001",
        "status": "completed",
        "rq": "RQ1",
        "seed": cfg.get("seed"),
        "fabricated": False,
        "dataset": data_loader.dataset_provenance(df, path),
        "config": cfg,
        "protocol": ev.get("protocol"),
        "beta": ev.get("beta"),
        "lag": ev.get("lag"),
        "n_train": ev.get("n_train"),
        "n_test": ev.get("n_test"),
        "boundary_purge": ev.get("boundary_purge"),
        "fingerprint": ev.get("fingerprint"),
        "vol_multiplier_stats": ev["vol_multiplier_stats"],
        "metrics": m,
        "comparison": ev["comparison"],
        "aux": ev.get("aux"),
        "protocol_legacy": ev.get("protocol_legacy"),
        "notes": [
            "B0 = historical rolling vol persistence (market only).",
            "E1 (oos_beta) = B0 + beta * (L_lag(vol_mult)-1); beta/lag fit on purged train, metrics on test.",
            "Train purge: exclude indices whose fwd_vol_horizon window crosses the train/test boundary.",
            "C1 = B0 + beta * shuffle(L_lag(vol_mult)-1) after beta is fixed (random control).",
            "protocol_legacy retains crude multiplicative E1=B0*(vol_mult/mean) for historical contrast.",
        ],
    }
    io_utils.write_json(RESULTS / "metrics.json", payload)
    rows = [
        {"model": k, **{f: m[k][f] for f in ("n", "mae", "rmse", "mape", "directional_accuracy")}}
        for k in ("B0", "E1", "C1")
    ]
    io_utils.write_csv(RESULTS / "metrics.csv", rows)
    io_utils.write_csv(RESULTS / "raw_results.csv", rows)

    plotting.save_bar_chart(
        PLOTS / "mae_rmse.png",
        ["B0 MAE", "E1 MAE", "C1 MAE", "B0 RMSE", "E1 RMSE", "C1 RMSE"],
        [m["B0"]["mae"], m["E1"]["mae"], m["C1"]["mae"], m["B0"]["rmse"], m["E1"]["rmse"], m["C1"]["rmse"]],
        title="EXP_001 — Vol forecast errors (lower better)",
        ylabel="Error",
    )
    plotting.save_bar_chart(
        RESULTS / "mae_rmse.png",
        ["B0 MAE", "E1 MAE", "C1 MAE"],
        [m["B0"]["mae"], m["E1"]["mae"], m["C1"]["mae"]],
        title="EXP_001 — MAE comparison",
        ylabel="MAE",
    )
    mask = np.isfinite(ev["y_true"]) & np.isfinite(ev["y_b0"]) & np.isfinite(ev["y_e1"])
    plotting.save_line_chart(
        PLOTS / "vol_forecast_sample.png",
        {"realized": ev["y_true"][mask], "B0": ev["y_b0"][mask], "E1": ev["y_e1"][mask]},
        title="EXP_001 — Forward vol vs forecasts",
        ylabel="vol",
    )
    plotting.save_line_chart(
        RESULTS / "vol_forecast_sample.png",
        {"realized": ev["y_true"][mask], "B0": ev["y_b0"][mask], "E1": ev["y_e1"][mask]},
        title="EXP_001 — Forward vol vs forecasts",
        ylabel="vol",
    )

    table = (
        "| Model | MAE | RMSE | Direction Accuracy |\n"
        "|------|-----|------|--------------------|\n"
        f"| B0 | {m['B0']['mae']:.6g} | {m['B0']['rmse']:.6g} | {m['B0']['directional_accuracy']:.4f} |\n"
        f"| E1 | {m['E1']['mae']:.6g} | {m['E1']['rmse']:.6g} | {m['E1']['directional_accuracy']:.4f} |\n"
        f"| C1 | {m['C1']['mae']:.6g} | {m['C1']['rmse']:.6g} | {m['C1']['directional_accuracy']:.4f} |\n"
    )
    leg = (ev.get("protocol_legacy") or {}).get("comparison_test_mask") or {}
    io_utils.write_markdown(
        EXP_DIR / "report.md",
        f"""# EXP_001 — Baseline vs ECONITH vs Random Control (RQ1)

## Mục tiêu
Đánh giá liệu context World (`macro_to_micro`) có cải thiện dự báo biến động so với market-only và so với random control.

## Protocol
- Primary: **oos_beta** (train_frac={train_frac}, beta={ev.get('beta')}, lag={ev.get('lag')}, fingerprint=`{ev.get('fingerprint')}`)
- Metrics reported on **test holdout only** (n_test={ev.get('n_test')}).
- Legacy multiplicative retained under `protocol_legacy` (test-mask contrast: E1>B0={leg.get('e1_better_mae_than_b0')}, pass_C1={leg.get('passes_random_control')}).

## Dataset
`{path}` — rows={len(df)}

## Kết quả (OOS test)

{table}

- MAE improvement E1 vs B0 (%): **{ev['comparison']['mae_improvement_pct_e1_vs_b0']}**
- Passes random control (E1 MAE < C1): **{ev['comparison']['passes_random_control']}**
- Δvol MAE E1: **{(ev.get('aux') or {}).get('delta_vol_mae', {}).get('E1')}**
- Regime DirAcc E1: **{(ev.get('aux') or {}).get('regime_directional_accuracy', {}).get('E1')}**
- vol_mult unique≈{ev['vol_multiplier_stats']['unique_approx']}, std={ev['vol_multiplier_stats']['std']}

## Limitations
Offline proxy; không phải full EventBus live coupling. Beta không được tune trên test.
""",
    )
    logger.info(
        "done unique=%s mae_b0=%.6g mae_e1=%.6g mae_c1=%.6g pass_ctrl=%s",
        ev["vol_multiplier_stats"]["unique_approx"],
        m["B0"]["mae"],
        m["E1"]["mae"],
        m["C1"]["mae"],
        ev["comparison"]["passes_random_control"],
    )
    return payload


if __name__ == "__main__":
    run()
