# EXP_002 — Calibration evaluation (RQ2)

## Mục tiêu
So sánh tham số **default** vs **calibrated** (`stochastic_coeffs.json`) theo khoảng cách moment với chuỗi macro thực.

## Trạng thái
**completed** — calibration_status=loaded

## Kết quả tóm tắt
- Features compared: 4
- Calibrated wins (lower moment L1): 4
- Default wins: 0

Chi tiết: `results/metrics.csv`, `results/metrics.json`.

## Giới hạn
- Mô phỏng OU+jump đơn biến, không phải full World multi-agent.
- Empirical inflation ưu tiên `macro_wb_inflation_cpi_pct` (YoY), không dùng diff index thô.
