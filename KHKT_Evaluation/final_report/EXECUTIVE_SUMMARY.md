# Executive Summary — ECONITH KHKT Evaluation

## ECONITH đã chứng minh được gì?

1. **RQ2 — Calibration 4/4:** EXP_002 `calibrated_wins_moment_l1 = 4` sau recalibrate trên đúng panel BTC+macro (mu khóa empirical mean, CPI YoY, cùng dt/seed). Cả 4/4 biến (`interest_rate`, `yield_10y`, `gdp_growth`, `inflation_cpi`) đều có Moment L1 thấp hơn sau hiệu chỉnh.
2. **RQ3 — Coupling đo được:** World ON vs OFF tạo khác biệt rõ (EXP_007); tín hiệu vị thế `sign(indicator_obi)` khi `used_real_obi=True` — không claim OBI nếu fallback.
3. **RQ4 — Tái lập:** `reproducible_rate = 1.0` trên **5 lần chạy** (EXP_009); EXP_005 lặp EXP_001 **2 lần** với fingerprint trùng (2/2). “100%” = tỷ lệ fingerprint khớp trong các lần đã chạy, không phải tuyên bố tuyệt đối ngoài protocol.
4. **Protocol RQ1 nâng cấp:** oos_beta (β/lag train-only, test OOS) + event pre/during/post + Δvol MAE / regime DirAcc + DM/bootstrap; legacy multiplicative giữ trong `protocol_legacy`.
## Chưa chứng minh / bằng chứng hỗn hợp?

1. **RQ1 full-panel:** EXP_001 `passes_random_control=False` (E1 chưa vượt B0/C1 trên toàn bộ test holdout).
2. **RQ1 event-windows:** E1 beats B0 = 2, beats C1 = 3 (trên 4 during-windows) — cải thiện cục bộ, không suy diễn phổ quát.
3. EXP_009: MAE(E1)−MAE(B0) = -3.0297277546498247e-07; DM p≈0.12493073932935106 (đọc kèm CI; không overclaim nếu chưa đạt ngưỡng chọn trước).
4. Alpha / digital twin / event feed chính thức: **không** thuộc phạm vi đã chứng minh.

## Giới hạn còn lại

- Offline proxy ≠ full EventBus SIMULATION.
- Chủ yếu BTC; chưa multi-asset.
- Risk proxy RQ3: `sign(indicator_obi)` chỉ khi metrics ghi `used_real_obi=true`; đọc delta, không claim PnL.
- Bootstrap + DM HAC là xấp xỉ phụ thuộc horizon.

## Snapshot lần chạy gần nhất

- EXP_001 protocol/beta/lag: oos_beta / 0.0013260131001123223 / 0
- EXP_001 passes_random_control: **False**
- EXP_006 e1_beats_b0 / e1_beats_c1: **2 / 3**
- EXP_002 calibrated_wins_moment_l1: **4**
- EXP_007 measurable scenarios: **3** (signal `indicator_obi`, used_real_obi=True)
- EXP_009 reproducible_rate: **1.0**

## Kết luận một câu cho hội đồng

> ECONITH chứng minh được **calibration 4/4**, **coupling có ảnh hưởng đo được**, và **protocol thí nghiệm tái lập (oos_beta + event windows + DM/bootstrap)**; **chưa** chứng minh World context cải thiện dự báo biến động một cách phổ quát trên full-panel BTC — dù một số cửa sổ sự kiện cho tín hiệu E1>B0/C1 — và báo cáo ghi nhận trung thực cả kết quả dương lẫn hỗn hợp.
