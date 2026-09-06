# Conclusions

## Có thể kết luận gì từ bộ thí nghiệm hiện tại?

1. **RQ2 (Calibration):** Calibrated thắng default theo moment L1 ở **4/4** feature (EXP_002; cùng seed, cùng dt, panel đồng bộ).
2. **RQ3 (Coupling):** World ON vs OFF tạo khác biệt đo được trên volatility/risk proxies (EXP_007).
3. **RQ4 (Reproducibility):** `reproducible_rate=1.0`; có bootstrap CI và Diebold–Mariano trên OOS.
4. **RQ1 (Forecast usefulness):** Protocol **oos_beta** thay multiplicative thô. Full-panel test: passes_random_control=False. Event during-windows: E1>B0=2, E1>C1=3 — tín hiệu cục bộ, chưa đủ để claim cải thiện phổ quát.

## Không thể kết luận gì?

- Không kết luận ECONITH “đánh bại thị trường” hay tạo alpha giao dịch.
- Không kết luận digital twin kinh tế toàn cầu.
- Không kết luận live Sentinel production behavior chỉ từ proxy threshold crossings.

## Status snapshot

| EXP | Status |
|---|---|
| EXP_001 | completed |
| EXP_002 | completed |
| EXP_006 | completed |
| EXP_008 | completed |
| EXP_007 | completed |
| EXP_009 | completed |
