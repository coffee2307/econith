# ECONITH — KHKT Evaluation Workspace

Thư mục này chứa toàn bộ cấu hình, script, log, kết quả và báo cáo phục vụ đánh giá khoa học kỹ thuật của ECONITH.

## Nguyên tắc

- **Không bịa số liệu.** Nếu thiếu dữ liệu → `status: blocked` + TODO.
- Mọi experiment **tái chạy được** qua config + script.
- Kết quả: CSV/JSON + PNG + Markdown.

## Cấu trúc

```text
KHKT_Evaluation/
  ANALYSIS.md
  README.md
  common/
  dataset_info/DATASET_DESCRIPTION.md
  experiments/EXP_001 … EXP_009/
  results/
  results_archive_pre_EXP006/   # kết quả cũ được lưu
  final_report/
    KHKT_Evaluation_Report.md
    EXECUTIVE_SUMMARY.md
    conclusions.md
    tables/summary.csv
    figures/*.png
  run_all.py
```

## Chạy tất cả

```powershell
cd f:\econith
python -m KHKT_Evaluation.run_all
```

Chỉ dựng lại báo cáo cuối + `results/research_summary.md` từ `metrics.json` trên disk (không chạy lại EXP):

```powershell
python -c "from KHKT_Evaluation.run_all import refresh_research_summary_from_disk; refresh_research_summary_from_disk()"
```

Hoặc chỉ final report:

```powershell
python -m KHKT_Evaluation.final_report.build_final_report
```

**RQ3 / OBI:** cột chuẩn trong feature store là `indicator_obi` (không phải `obi`). Metrics EXP_003/007 ghi `signal.used_real_obi` — chỉ claim OBI thật khi `true`. Không lấy số chương kết quả từ `research_summary.md` cũ nếu lệch `metrics.json`.
