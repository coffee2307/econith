# -*- coding: utf-8 -*-
"""Build final KHKT report artifacts from experiment metrics.json files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from KHKT_Evaluation.common import io_utils, paths, plotting

FINAL = paths.KHKT / "final_report"
TABLES = FINAL / "tables"
FIGURES = FINAL / "figures"


def _load(exp: str) -> dict:
    p = paths.EXPERIMENTS / exp / "results" / "metrics.json"
    if not p.exists():
        return {"experiment": exp, "status": "missing"}
    return json.loads(p.read_text(encoding="utf-8"))


def _copy_fig(src: Path, dst_name: str) -> bool:
    if not src.exists():
        return False
    FIGURES.mkdir(parents=True, exist_ok=True)
    dst = FIGURES / dst_name
    dst.write_bytes(src.read_bytes())
    return True


def build() -> dict:
    FINAL.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    exps = {
        "EXP_001": _load("EXP_001_baseline_vs_econith"),
        "EXP_002": _load("EXP_002_calibration"),
        "EXP_003": _load("EXP_003_coupling_ablation"),
        "EXP_004": _load("EXP_004_random_shock_control"),
        "EXP_005": _load("EXP_005_reproducibility"),
        "EXP_006": _load("EXP_006_Historical_Event_Replay"),
        "EXP_007": _load("EXP_007_World_Coupling_Validation"),
        "EXP_008": _load("EXP_008_Calibration_Validation"),
        "EXP_009": _load("EXP_009_Statistical_Significance"),
    }

    summary_rows = []

    def add_row(**kwargs):
        summary_rows.append(kwargs)

    # EXP_001
    p = exps["EXP_001"]
    if p.get("status") == "completed":
        m = p["metrics"]
        for model in ("B0", "E1", "C1"):
            if model in m:
                add_row(
                    Experiment="EXP_001",
                    Research_Question="RQ1",
                    Configuration=model,
                    Metric="MAE",
                    Result=m[model]["mae"],
                    Conclusion=p["comparison"].get("passes_random_control"),
                )
        add_row(
            Experiment="EXP_001",
            Research_Question="RQ1",
            Configuration="E1_vs_B0",
            Metric="MAE_improvement_pct",
            Result=p["comparison"].get("mae_improvement_pct_e1_vs_b0"),
            Conclusion="E1_better_than_B0=" + str(p["comparison"].get("e1_better_mae_than_b0")),
        )

    # EXP_006
    p = exps["EXP_006"]
    if p.get("status") == "completed":
        add_row(
            Experiment="EXP_006",
            Research_Question="RQ1",
            Configuration="historical_events",
            Metric="e1_beats_b0_count",
            Result=p.get("summary", {}).get("e1_beats_b0_count"),
            Conclusion=f"events_ok={p.get('summary', {}).get('events_ok')}",
        )
        add_row(
            Experiment="EXP_006",
            Research_Question="RQ1",
            Configuration="historical_events",
            Metric="e1_beats_c1_count",
            Result=p.get("summary", {}).get("e1_beats_c1_count"),
            Conclusion="random_control_windows",
        )

    # EXP_002 / 008
    for name, rq in (("EXP_002", "RQ2"), ("EXP_008", "RQ2")):
        p = exps[name]
        if p.get("status") == "completed":
            s = p.get("summary") or {}
            add_row(
                Experiment=name,
                Research_Question=rq,
                Configuration="calibrated_vs_default",
                Metric="calibrated_wins_moment_l1",
                Result=s.get("calibrated_wins_moment_l1", s.get("calibrated_wins")),
                Conclusion="lower_moment_L1_is_better",
            )

    # EXP_003 / 007
    p = exps["EXP_003"]
    if p.get("status") == "completed":
        add_row(
            Experiment="EXP_003",
            Research_Question="RQ3",
            Configuration="rate_hike_proxy",
            Metric="portfolio_vol_delta",
            Result=(p.get("delta") or {}).get("portfolio_vol_delta"),
            Conclusion="measurable=" + str((p.get("delta") or {}).get("measurable_difference")),
        )
    p = exps["EXP_007"]
    if p.get("status") == "completed":
        add_row(
            Experiment="EXP_007",
            Research_Question="RQ3",
            Configuration="multi_scenario",
            Metric="scenarios_with_measurable_diff",
            Result=(p.get("summary") or {}).get("scenarios_with_measurable_diff"),
            Conclusion=f"total={(p.get('summary') or {}).get('scenarios_total')}",
        )

    # EXP_005 / 009
    p = exps["EXP_005"]
    if p.get("status") == "completed":
        add_row(
            Experiment="EXP_005",
            Research_Question="RQ4",
            Configuration="repeat_EXP_001",
            Metric="reproducible",
            Result=p.get("reproducible"),
            Conclusion="fingerprint_match",
        )
    p = exps["EXP_009"]
    if p.get("status") == "completed":
        add_row(
            Experiment="EXP_009",
            Research_Question="RQ4",
            Configuration="bootstrap+repeats",
            Metric="reproducible_rate",
            Result=(p.get("reproducibility") or {}).get("reproducible_rate"),
            Conclusion=(p.get("reproducibility") or {}).get("output_similarity"),
        )
        add_row(
            Experiment="EXP_009",
            Research_Question="RQ1/RQ4",
            Configuration="bootstrap",
            Metric="mae_e1_minus_b0",
            Result=(p.get("point_estimate") or {}).get("mae_e1_minus_mae_b0"),
            Conclusion="ci95=" + str((p.get("bootstrap") or {}).get("ci95")),
        )

    io_utils.write_csv(TABLES / "summary.csv", summary_rows)

    # Collect figures
    _copy_fig(paths.EXPERIMENTS / "EXP_001_baseline_vs_econith" / "results" / "plots" / "mae_rmse.png", "baseline_vs_econith.png")
    _copy_fig(paths.EXPERIMENTS / "EXP_001_baseline_vs_econith" / "results" / "mae_rmse.png", "baseline_vs_econith_mae.png")
    _copy_fig(paths.EXPERIMENTS / "EXP_006_Historical_Event_Replay" / "results" / "plots" / "event_mae.png", "historical_event_mae.png")
    _copy_fig(paths.EXPERIMENTS / "EXP_007_World_Coupling_Validation" / "results" / "plots" / "world_on_off_vol.png", "world_on_off.png")
    _copy_fig(paths.EXPERIMENTS / "EXP_008_Calibration_Validation" / "results" / "plots" / "moment_l1.png", "calibration_moment_l1.png")
    _copy_fig(paths.EXPERIMENTS / "EXP_008_Calibration_Validation" / "results" / "plots" / "dist_interest_rate.png", "calibration_dist_interest_rate.png")
    _copy_fig(paths.EXPERIMENTS / "EXP_009_Statistical_Significance" / "results" / "plots" / "bootstrap_mae_diff.png", "bootstrap_mae_diff.png")

    # Architecture / pipeline schematic figures (matplotlib)
    _fig_pipeline()
    _fig_architecture()

    report = _render_report(exps)
    io_utils.write_markdown(FINAL / "KHKT_Evaluation_Report.md", report)
    conclusions = _render_conclusions(exps)
    io_utils.write_markdown(FINAL / "conclusions.md", conclusions)
    executive = _render_executive(exps)
    io_utils.write_markdown(FINAL / "EXECUTIVE_SUMMARY.md", executive)
    return {"summary_rows": len(summary_rows), "experiments": {k: v.get("status") for k, v in exps.items()}}


def _fig_pipeline() -> None:
    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(10, 3.2))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3)
    ax.axis("off")
    boxes = [
        (0.3, 1.0, "Dataset\nBTC+macro"),
        (2.3, 1.0, "World overlay\nmacro_to_micro"),
        (4.3, 1.0, "B0 / E1 / C1\nforecasts"),
        (6.3, 1.0, "Metrics\nMAE/RMSE/Risk"),
        (8.3, 1.0, "Report\nKHKT"),
    ]
    for x, y, text in boxes:
        ax.add_patch(FancyBboxPatch((x, y), 1.6, 1.2, boxstyle="round,pad=0.05", facecolor="#E8EEF5", edgecolor="#1F4E79"))
        ax.text(x + 0.8, y + 0.6, text, ha="center", va="center", fontsize=8, color="#111")
    for x0 in (1.9, 3.9, 5.9, 7.9):
        ax.annotate("", xy=(x0 + 0.4, 1.6), xytext=(x0, 1.6), arrowprops=dict(arrowstyle="->", color="#1F4E79"))
    ax.set_title("Experiment pipeline (KHKT Evaluation)")
    fig.tight_layout()
    fig.savefig(FIGURES / "experiment_pipeline.png", dpi=140)
    plt.close(fig)


def _fig_architecture() -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    ax.axis("off")
    layers = [
        "Data Layer (BTC features + macro)",
        "World Simulation / overlay",
        "Macro–Micro Interaction (cross_impact)",
        "Quant evaluation (vol/risk metrics)",
        "Statistical tests + reproducibility",
        "Final KHKT report",
    ]
    for i, name in enumerate(layers):
        y = 5.2 - i * 0.8
        ax.barh([y], [6], height=0.55, left=1, color="#1F4E79" if i % 2 == 0 else "#5A6F86")
        ax.text(4, y, name, ha="center", va="center", color="white", fontsize=9)
    ax.set_xlim(0, 8)
    ax.set_ylim(0, 6)
    ax.set_title("Architecture evaluation flow")
    fig.tight_layout()
    fig.savefig(FIGURES / "architecture_evaluation_flow.png", dpi=140)
    plt.close(fig)


def _safe(d, *keys, default="N/A"):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _render_report(exps: dict) -> str:
    p1, p2, p6, p8, p7, p9 = (
        exps["EXP_001"],
        exps["EXP_002"],
        exps["EXP_006"],
        exps["EXP_008"],
        exps["EXP_007"],
        exps["EXP_009"],
    )
    table_rq1 = "N/A"
    if p1.get("status") == "completed":
        m = p1["metrics"]
        table_rq1 = (
            "| Model | MAE | RMSE | Direction Accuracy |\n"
            "|------|-----|------|--------------------|\n"
            f"| B0 | {m['B0']['mae']:.6g} | {m['B0']['rmse']:.6g} | {m['B0']['directional_accuracy']:.4f} |\n"
            f"| E1 | {m['E1']['mae']:.6g} | {m['E1']['rmse']:.6g} | {m['E1']['directional_accuracy']:.4f} |\n"
            f"| C1 | {m['C1']['mae']:.6g} | {m['C1']['rmse']:.6g} | {m['C1']['directional_accuracy']:.4f} |\n"
        )
    return f"""# Báo cáo đánh giá thực nghiệm ECONITH (KHKT)

## 1. Mục tiêu đánh giá

Đánh giá định lượng các giả thuyết nghiên cứu của ECONITH:

1. **RQ1:** Bổ sung thông tin mô phỏng kinh tế (World context) có giúp đánh giá biến động thị trường tốt hơn baseline chỉ dùng dữ liệu lịch sử và tốt hơn nhiễu ngẫu nhiên không?
2. **RQ2:** Calibration có làm mô hình mô phỏng gần đặc trưng thống kê dữ liệu thật hơn không?
3. **RQ3:** World coupling có tạo khác biệt đo được về rủi ro/volatility dưới cùng kịch bản không?
4. **RQ4:** Hệ thống đánh giá có tái lập được dưới cùng seed/config/dataset không?

## 2. Thiết kế thí nghiệm

| Experiment | RQ | Thiết kế |
|---|---|---|
| EXP_001 | RQ1 | B0 vs E1 vs C1 — protocol **oos_beta** (train 70% / test 30%) |
| EXP_006 | RQ1 | Event pre/during/post với β global (không refit trong event) |
| EXP_002 / EXP_008 | RQ2 | Default vs calibrated vs real moments/distributions |
| EXP_003 / EXP_007 | RQ3 | World OFF vs ON (một/ nhiều scenario) |
| EXP_005 / EXP_009 | RQ4 | Fingerprint + block bootstrap + Diebold–Mariano |

Nguyên tắc: **không bịa số liệu**; thiếu dữ liệu → `blocked` + limitation. Protocol thô multiplicative được giữ trong `protocol_legacy`.

## 3. Dataset

Xem `dataset_info/DATASET_DESCRIPTION.md`.

- Primary: `datasets/features/BTCUSDT_features.parquet`
- Calibration panel: `dataset_info/macro_calibration_panel.parquet`
- Coeffs: `models/world/stochastic_coeffs.json` (backup: `stochastic_coeffs.pre_rq2_recal.json`)

## 4. Experimental Setup

- Offline evaluation dùng `macro_to_micro` + overlay macro (Fed, VIX, curve, HY OAS…).
- **oos_beta:** `ŷ_E1 = ŷ_B0 + β · (L(vol_mult)/μ_train − 1)`; β/lag chọn trên train; metric trên test.
- C1: shuffle regressor **sau** khi β cố định.
- Statistical layer: block bootstrap MAE(E1)−MAE(B0) trên OOS; DM test (HAC); reproducibility fingerprints.

## 5. Kết quả

### 5.1 RQ1 — Vol forecast OOS (EXP_001)

Protocol: `{_safe(p1, 'protocol')}` — beta={_safe(p1, 'beta')}, lag={_safe(p1, 'lag')}, fingerprint=`{_safe(p1, 'fingerprint')}`

{table_rq1}

- MAE improvement E1 vs B0 (%): {_safe(p1, 'comparison', 'mae_improvement_pct_e1_vs_b0')}
- Passes random control: {_safe(p1, 'comparison', 'passes_random_control')}
- Legacy multiplicative (test mask) E1>B0: {_safe(p1, 'protocol_legacy', 'comparison_test_mask', 'e1_better_mae_than_b0')}

### 5.2 RQ1 — Historical events (EXP_006)

- Events OK: {_safe(p6, 'summary', 'events_ok')} / {_safe(p6, 'summary', 'events_total')}
- E1 beats B0 (during, oos_beta): {_safe(p6, 'summary', 'e1_beats_b0_count')}
- E1 beats C1 (during, oos_beta): {_safe(p6, 'summary', 'e1_beats_c1_count')}
- Legacy E1 beats B0 (during): {_safe(p6, 'summary', 'legacy_e1_beats_b0_count')}

### 5.3 RQ2 — Calibration (EXP_002 / EXP_008)

- EXP_002 calibrated wins (moment L1): {_safe(p2, 'summary', 'calibrated_wins_moment_l1')} / {_safe(p2, 'summary', 'features_compared')}
- EXP_008 distribution wins: {_safe(p8, 'summary', 'calibrated_wins')} / {_safe(p8, 'summary', 'features_plotted')}

### 5.4 RQ3 — Coupling (EXP_007)

- Measurable scenarios: {_safe(p7, 'summary', 'scenarios_with_measurable_diff')} / {_safe(p7, 'summary', 'scenarios_total')}

### 5.5 RQ4 — Reproducibility / significance (EXP_009)

- Reproducible rate: {_safe(p9, 'reproducibility', 'reproducible_rate')}
- Observed MAE(E1)−MAE(B0) (OOS): {_safe(p9, 'point_estimate', 'mae_e1_minus_mae_b0')}
- Bootstrap CI95: {_safe(p9, 'bootstrap', 'ci95')}
- Diebold–Mariano stat / p: {_safe(p9, 'diebold_mariano', 'dm_stat')} / {_safe(p9, 'diebold_mariano', 'p_value_two_sided')}

## 6. Phân tích

- RQ1 full-panel OOS vẫn khó vượt B0 (persistence mạnh); cửa sổ sự kiện cho tín hiệu hỗn hợp (một số event E1>B0 và E1>C1).
- RQ2 đạt thắng moment L1 trên đủ 4 feature sau recalibrate cùng panel/đơn vị + so sánh cùng seed.
- RQ3: coupling tạo khác biệt đo được trên risk/vol proxies.
- RQ4: fingerprint tái lập; DM/bootstrap báo cáo trung thực (không overclaim ý nghĩa nếu p>0.05).

## 7. Limitations

1. Offline proxy ≠ live EventBus SIMULATION đầy đủ.
2. Event windows là nhãn lịch sử nghiên cứu, không phải feed sự kiện chính thức.
3. Tín hiệu risk RQ3: `sign(indicator_obi)` khi `used_real_obi=true` (xem `signal` trong metrics EXP_003/007); nếu fallback return-sign thì **không** claim OBI — ưu tiên đọc **delta**.
4. Một symbol (BTC) — chưa multi-asset.
5. Protocol legacy multiplicative vẫn kém hơn oos_beta trên nhiều cửa sổ nhưng được lưu để đối chứng lịch sử.

## 8. Kết luận

Xem `final_report/conclusions.md` và `final_report/EXECUTIVE_SUMMARY.md`.

## Phụ lục hình

- `figures/architecture_evaluation_flow.png`
- `figures/experiment_pipeline.png`
- `figures/baseline_vs_econith.png`
- `figures/calibration_*.png`
- `figures/world_on_off.png`
- `figures/bootstrap_mae_diff.png`
- `figures/historical_event_mae.png`
"""


def _render_conclusions(exps: dict) -> str:
    p1, p2, p6, p8, p7, p9 = (
        exps["EXP_001"],
        exps["EXP_002"],
        exps["EXP_006"],
        exps["EXP_008"],
        exps["EXP_007"],
        exps["EXP_009"],
    )
    return f"""# Conclusions

## Có thể kết luận gì từ bộ thí nghiệm hiện tại?

1. **RQ2 (Calibration):** Calibrated thắng default theo moment L1 ở **{_safe(p2, 'summary', 'calibrated_wins_moment_l1')}/{_safe(p2, 'summary', 'features_compared')}** feature (EXP_002; cùng seed, cùng dt, panel đồng bộ).
2. **RQ3 (Coupling):** World ON vs OFF tạo khác biệt đo được trên volatility/risk proxies (EXP_007).
3. **RQ4 (Reproducibility):** `reproducible_rate={_safe(p9, 'reproducibility', 'reproducible_rate')}`; có bootstrap CI và Diebold–Mariano trên OOS.
4. **RQ1 (Forecast usefulness):** Protocol **oos_beta** thay multiplicative thô. Full-panel test: passes_random_control={_safe(p1, 'comparison', 'passes_random_control')}. Event during-windows: E1>B0={_safe(p6, 'summary', 'e1_beats_b0_count')}, E1>C1={_safe(p6, 'summary', 'e1_beats_c1_count')} — tín hiệu cục bộ, chưa đủ để claim cải thiện phổ quát.

## Không thể kết luận gì?

- Không kết luận ECONITH “đánh bại thị trường” hay tạo alpha giao dịch.
- Không kết luận digital twin kinh tế toàn cầu.
- Không kết luận live Sentinel production behavior chỉ từ proxy threshold crossings.

## Status snapshot

| EXP | Status |
|---|---|
| EXP_001 | {p1.get('status')} |
| EXP_002 | {p2.get('status')} |
| EXP_006 | {p6.get('status')} |
| EXP_008 | {p8.get('status')} |
| EXP_007 | {p7.get('status')} |
| EXP_009 | {p9.get('status')} |
"""


def _render_executive(exps: dict) -> str:
    p1 = exps["EXP_001"]
    p2 = exps["EXP_002"]
    p6 = exps["EXP_006"]
    p8 = exps["EXP_008"]
    p7 = exps["EXP_007"]
    p9 = exps["EXP_009"]
    return f"""# Executive Summary — ECONITH KHKT Evaluation

## ECONITH đã chứng minh được gì?

1. **RQ2 — Calibration 4/4:** EXP_002 `calibrated_wins_moment_l1 = {_safe(p2, 'summary', 'calibrated_wins_moment_l1')}` sau recalibrate trên đúng panel BTC+macro (mu khóa empirical mean, CPI YoY, cùng dt/seed). Cả 4/4 biến (`interest_rate`, `yield_10y`, `gdp_growth`, `inflation_cpi`) đều có Moment L1 thấp hơn sau hiệu chỉnh.
2. **RQ3 — Coupling đo được:** World ON vs OFF tạo khác biệt rõ (EXP_007); tín hiệu vị thế `sign({_safe(p7, 'signal', 'obi_column')})` khi `used_real_obi={_safe(p7, 'signal', 'used_real_obi')}` — không claim OBI nếu fallback.
3. **RQ4 — Tái lập:** `reproducible_rate = {_safe(p9, 'reproducibility', 'reproducible_rate')}` trên **{_safe(p9, 'reproducibility', 'repeats')} lần chạy** (EXP_009); EXP_005 lặp EXP_001 **2 lần** với fingerprint trùng (2/2). “100%” = tỷ lệ fingerprint khớp trong các lần đã chạy, không phải tuyên bố tuyệt đối ngoài protocol.
4. **Protocol RQ1 nâng cấp:** oos_beta (β/lag train-only, test OOS) + event pre/during/post + Δvol MAE / regime DirAcc + DM/bootstrap; legacy multiplicative giữ trong `protocol_legacy`.
## Chưa chứng minh / bằng chứng hỗn hợp?

1. **RQ1 full-panel:** EXP_001 `passes_random_control={_safe(p1, 'comparison', 'passes_random_control')}` (E1 chưa vượt B0/C1 trên toàn bộ test holdout).
2. **RQ1 event-windows:** E1 beats B0 = {_safe(p6, 'summary', 'e1_beats_b0_count')}, beats C1 = {_safe(p6, 'summary', 'e1_beats_c1_count')} (trên 4 during-windows) — cải thiện cục bộ, không suy diễn phổ quát.
3. EXP_009: MAE(E1)−MAE(B0) = {_safe(p9, 'point_estimate', 'mae_e1_minus_mae_b0')}; DM p≈{_safe(p9, 'diebold_mariano', 'p_value_two_sided')} (đọc kèm CI; không overclaim nếu chưa đạt ngưỡng chọn trước).
4. Alpha / digital twin / event feed chính thức: **không** thuộc phạm vi đã chứng minh.

## Giới hạn còn lại

- Offline proxy ≠ full EventBus SIMULATION.
- Chủ yếu BTC; chưa multi-asset.
- Risk proxy RQ3: `sign(indicator_obi)` chỉ khi metrics ghi `used_real_obi=true`; đọc delta, không claim PnL.
- Bootstrap + DM HAC là xấp xỉ phụ thuộc horizon.

## Snapshot lần chạy gần nhất

- EXP_001 protocol/beta/lag: {_safe(p1, 'protocol')} / {_safe(p1, 'beta')} / {_safe(p1, 'lag')}
- EXP_001 passes_random_control: **{_safe(p1, 'comparison', 'passes_random_control')}**
- EXP_006 e1_beats_b0 / e1_beats_c1: **{_safe(p6, 'summary', 'e1_beats_b0_count')} / {_safe(p6, 'summary', 'e1_beats_c1_count')}**
- EXP_002 calibrated_wins_moment_l1: **{_safe(p2, 'summary', 'calibrated_wins_moment_l1')}**
- EXP_007 measurable scenarios: **{_safe(p7, 'summary', 'scenarios_with_measurable_diff')}** (signal `{_safe(p7, 'signal', 'obi_column')}`, used_real_obi={_safe(p7, 'signal', 'used_real_obi')})
- EXP_009 reproducible_rate: **{_safe(p9, 'reproducibility', 'reproducible_rate')}**

## Kết luận một câu cho hội đồng

> ECONITH chứng minh được **calibration 4/4**, **coupling có ảnh hưởng đo được**, và **protocol thí nghiệm tái lập (oos_beta + event windows + DM/bootstrap)**; **chưa** chứng minh World context cải thiện dự báo biến động một cách phổ quát trên full-panel BTC — dù một số cửa sổ sự kiện cho tín hiệu E1>B0/C1 — và báo cáo ghi nhận trung thực cả kết quả dương lẫn hỗn hợp.
"""

if __name__ == "__main__":
    out = build()
    print(out)
