# Research Summary — ECONITH KHKT Evaluation

> Generated from each experiment `results/metrics.json` on disk (not hand-edited).  
> Refresh: `python -m KHKT_Evaluation.run_all` or `python -c "from KHKT_Evaluation.run_all import refresh_research_summary_from_disk; refresh_research_summary_from_disk()"`.  
> **Nguyên tắc:** không bịa số liệu. Khi viết chương kết quả, lấy số từ từng `metrics.json` đã xác nhận hoặc tái tạo file này.

## Status

| EXP | Status | RQ |
|---|---|---|
| EXP_001 | completed | RQ1 |
| EXP_002 | completed | RQ2 |
| EXP_003 | completed | RQ3 |
| EXP_004 | completed | RQ1 control |
| EXP_005 | completed | RQ4 |
| EXP_006 | completed | RQ1 events |
| EXP_007 | completed | RQ3 multi-scenario |
| EXP_008 | completed | RQ2 distributions |
| EXP_009 | completed | RQ4 stats |

## Key numbers

### RQ1
- EXP_001 MAE B0/E1/C1: 0.001578296621397314 / 0.001579293877644381 / 0.0015784398707533803
- Passes random control: False
- Protocol: oos_beta
- vol_mult unique≈510
- EXP_006 e1_beats_b0 / e1_beats_c1: 2 / 3

### RQ2
- EXP_002 calibrated wins (moment L1): 4 / 4
- EXP_008 calibrated wins (distributions): 3 / 4

### RQ3
- EXP_003 measurable: True; vol_delta=0.004185397598703289
- EXP_003 signal: source=sign_obi; column=indicator_obi; used_real_obi=True
- EXP_007 measurable scenarios: 3 / 3
- EXP_007 signal: source=sign_obi; column=indicator_obi; used_real_obi=True
- **Rule:** chỉ mô tả thí nghiệm dùng OBI thật khi `used_real_obi=true` (cột chuẩn: `indicator_obi`).

### RQ4
- EXP_005 reproducible: True
- EXP_009 reproducible_rate: 1.0
- EXP_009 MAE(E1)-MAE(B0): -3.0297277546498247e-07

## Final report

See `KHKT_Evaluation/final_report/KHKT_Evaluation_Report.md` and `EXECUTIVE_SUMMARY.md`.
