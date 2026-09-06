# -*- coding: utf-8 -*-
"""Run all KHKT experiments sequentially and refresh reports."""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import io_utils, paths
from KHKT_Evaluation.experiments.EXP_001_baseline_vs_econith import run as e1
from KHKT_Evaluation.experiments.EXP_002_calibration import run as e2
from KHKT_Evaluation.experiments.EXP_003_coupling_ablation import run as e3
from KHKT_Evaluation.experiments.EXP_004_random_shock_control import run as e4
from KHKT_Evaluation.experiments.EXP_005_reproducibility import run as e5
from KHKT_Evaluation.experiments.EXP_006_Historical_Event_Replay import run as e6
from KHKT_Evaluation.experiments.EXP_007_World_Coupling_Validation import run as e7
from KHKT_Evaluation.experiments.EXP_008_Calibration_Validation import run as e8
from KHKT_Evaluation.experiments.EXP_009_Statistical_Significance import run as e9
from KHKT_Evaluation.final_report.build_final_report import build as build_final

import json


EXPS = [
    ("EXP_001", e1.run),
    ("EXP_002", e2.run),
    ("EXP_003", e3.run),
    ("EXP_004", e4.run),
    ("EXP_005", e5.run),
    ("EXP_006", e6.run),
    ("EXP_007", e7.run),
    ("EXP_008", e8.run),
    ("EXP_009", e9.run),
]


def main() -> None:
    logger = io_utils.setup_logger("KHKT_ALL", paths.LOGS / "run_all.log")
    summary_rows = []
    payloads = {}
    for name, fn in EXPS:
        logger.info("==== running %s ====", name)
        try:
            out = fn()
            payloads[name] = out
            summary_rows.append(
                {
                    "experiment": name,
                    "status": out.get("status"),
                    "rq": out.get("rq"),
                    "fabricated": out.get("fabricated", False),
                }
            )
            logger.info("%s -> %s", name, out.get("status"))
        except Exception as exc:  # noqa: BLE001
            logger.error("%s FAILED: %s\n%s", name, exc, traceback.format_exc())
            payloads[name] = {
                "experiment": name,
                "status": "error",
                "error": str(exc),
                "fabricated": False,
            }
            summary_rows.append(
                {"experiment": name, "status": "error", "rq": None, "fabricated": False}
            )

    # Authoritative summary: always re-read each experiment's metrics.json on disk
    # so research_summary cannot drift from stale in-memory / partial runs.
    disk_payloads = load_all_experiment_metrics()
    io_utils.write_json(paths.RESULTS / "all_experiments.json", disk_payloads)
    io_utils.write_csv(
        paths.RESULTS / "all_experiments.csv",
        [
            {
                "experiment": k,
                "status": v.get("status"),
                "rq": v.get("rq"),
                "fabricated": v.get("fabricated", False),
            }
            for k, v in disk_payloads.items()
        ],
    )
    _write_research_summary(disk_payloads)

    logger.info("==== building final_report ====")
    try:
        final_meta = build_final()
        logger.info("final_report ok: %s", final_meta)
    except Exception as exc:  # noqa: BLE001
        logger.error("final_report FAILED: %s\n%s", exc, traceback.format_exc())

    logger.info("done — see KHKT_Evaluation/results/ and final_report/")


EXP_FOLDERS = {
    "EXP_001": "EXP_001_baseline_vs_econith",
    "EXP_002": "EXP_002_calibration",
    "EXP_003": "EXP_003_coupling_ablation",
    "EXP_004": "EXP_004_random_shock_control",
    "EXP_005": "EXP_005_reproducibility",
    "EXP_006": "EXP_006_Historical_Event_Replay",
    "EXP_007": "EXP_007_World_Coupling_Validation",
    "EXP_008": "EXP_008_Calibration_Validation",
    "EXP_009": "EXP_009_Statistical_Significance",
}


def load_all_experiment_metrics() -> dict:
    """Load each EXP ``results/metrics.json`` — source of truth for Chapter / summary tables."""
    out = {}
    for key, folder in EXP_FOLDERS.items():
        path = paths.EXPERIMENTS / folder / "results" / "metrics.json"
        if not path.exists():
            out[key] = {"experiment": key, "status": "missing", "fabricated": False}
            continue
        out[key] = json.loads(path.read_text(encoding="utf-8"))
    return out


def refresh_research_summary_from_disk() -> None:
    """Rebuild research_summary.md + all_experiments.json from on-disk metrics only."""
    payloads = load_all_experiment_metrics()
    io_utils.write_json(paths.RESULTS / "all_experiments.json", payloads)
    _write_research_summary(payloads)
    build_final()


def _safe(d, *keys, default="N/A"):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _write_research_summary(payloads: dict) -> None:
    p1 = payloads.get("EXP_001", {})
    p2 = payloads.get("EXP_002", {})
    p3 = payloads.get("EXP_003", {})
    p4 = payloads.get("EXP_004", {})
    p5 = payloads.get("EXP_005", {})
    p6 = payloads.get("EXP_006", {})
    p7 = payloads.get("EXP_007", {})
    p8 = payloads.get("EXP_008", {})
    p9 = payloads.get("EXP_009", {})

    text = f"""# Research Summary — ECONITH KHKT Evaluation

> Generated from each experiment `results/metrics.json` on disk (not hand-edited).  
> Refresh: `python -m KHKT_Evaluation.run_all` or `python -c "from KHKT_Evaluation.run_all import refresh_research_summary_from_disk; refresh_research_summary_from_disk()"`.  
> **Nguyên tắc:** không bịa số liệu. Khi viết chương kết quả, lấy số từ từng `metrics.json` đã xác nhận hoặc tái tạo file này.

## Status

| EXP | Status | RQ |
|---|---|---|
| EXP_001 | {_safe(p1, 'status')} | RQ1 |
| EXP_002 | {_safe(p2, 'status')} | RQ2 |
| EXP_003 | {_safe(p3, 'status')} | RQ3 |
| EXP_004 | {_safe(p4, 'status')} | RQ1 control |
| EXP_005 | {_safe(p5, 'status')} | RQ4 |
| EXP_006 | {_safe(p6, 'status')} | RQ1 events |
| EXP_007 | {_safe(p7, 'status')} | RQ3 multi-scenario |
| EXP_008 | {_safe(p8, 'status')} | RQ2 distributions |
| EXP_009 | {_safe(p9, 'status')} | RQ4 stats |

## Key numbers

### RQ1
- EXP_001 MAE B0/E1/C1: {_safe(p1, 'metrics', 'B0', 'mae')} / {_safe(p1, 'metrics', 'E1', 'mae')} / {_safe(p1, 'metrics', 'C1', 'mae')}
- Passes random control: {_safe(p1, 'comparison', 'passes_random_control')}
- Protocol: {_safe(p1, 'protocol')}; n_train/n_test={_safe(p1, 'n_train')}/{_safe(p1, 'n_test')}
- Boundary purge: {_safe(p1, 'boundary_purge', 'n_train_after_purge')} train rows after excluding horizon-crossing windows
- vol_mult unique≈{_safe(p1, 'vol_multiplier_stats', 'unique_approx')}
- EXP_006 e1_beats_b0 / e1_beats_c1: {_safe(p6, 'summary', 'e1_beats_b0_count')} / {_safe(p6, 'summary', 'e1_beats_c1_count')}

### RQ2
- EXP_002 calibrated wins (moment L1): {_safe(p2, 'summary', 'calibrated_wins_moment_l1')} / {_safe(p2, 'summary', 'features_compared')}
- EXP_008 calibrated wins (distributions): {_safe(p8, 'summary', 'calibrated_wins')} / {_safe(p8, 'summary', 'features_plotted')}

### RQ3
- EXP_003 measurable (C vs A): {_safe(p3, 'delta', 'measurable_difference')}; vol_delta={_safe(p3, 'delta', 'portfolio_vol_delta')}
- EXP_003 ON_no_shock vs OFF: {_safe(p3, 'delta_B_vs_A_on_no_shock', 'measurable_difference')}; rate shock changes Mt/Ot: {_safe(p3, 'delta_C_vs_B_rate_shock', 'shock_changes_Mt_or_Ot')}
- EXP_003 signal: source={_safe(p3, 'signal', 'signal_source')}; column={_safe(p3, 'signal', 'obi_column')}; used_real_obi={_safe(p3, 'signal', 'used_real_obi')}
- EXP_007 measurable scenarios: {_safe(p7, 'summary', 'scenarios_with_measurable_diff')} / {_safe(p7, 'summary', 'scenarios_total')}
- EXP_007 ON_no_shock vs OFF: {_safe(p7, 'summary', 'on_no_shock_measurable_vs_off')}; interest_rate ΔMt/Ot vs ON₀: {_safe(p7, 'summary', 'interest_rate_changes_Mt_Ot_vs_on_no_shock')}
- EXP_007 signal: source={_safe(p7, 'signal', 'signal_source')}; column={_safe(p7, 'signal', 'obi_column')}; used_real_obi={_safe(p7, 'signal', 'used_real_obi')}
- **Rule:** chỉ mô tả thí nghiệm dùng OBI thật khi `used_real_obi=true` (cột chuẩn: `indicator_obi`).

### RQ4
- EXP_005 reproducible: {_safe(p5, 'reproducible')}
- EXP_009 reproducible_rate: {_safe(p9, 'reproducibility', 'reproducible_rate')}
- EXP_009 MAE(E1)-MAE(B0): {_safe(p9, 'point_estimate', 'mae_e1_minus_mae_b0')}

## Final report

See `KHKT_Evaluation/final_report/KHKT_Evaluation_Report.md` and `EXECUTIVE_SUMMARY.md`.
"""
    io_utils.write_markdown(paths.RESULTS / "research_summary.md", text)


if __name__ == "__main__":
    main()
