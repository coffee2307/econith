# Báo cáo đánh giá thực nghiệm ECONITH (KHKT)

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

Protocol: `oos_beta` — beta=0.0013260131001123223, lag=0, fingerprint=`f5a8e1af47a486a9`

| Model | MAE | RMSE | Direction Accuracy |
|------|-----|------|--------------------|
| B0 | 0.0015783 | 0.00213023 | 0.3450 |
| E1 | 0.00157929 | 0.00213127 | 0.3455 |
| C1 | 0.00157844 | 0.00213029 | 0.3772 |


- MAE improvement E1 vs B0 (%): -0.0631856036151342
- Passes random control: False
- Legacy multiplicative (test mask) E1>B0: False

### 5.2 RQ1 — Historical events (EXP_006)

- Events OK: 4 / 4
- E1 beats B0 (during, oos_beta): 2
- E1 beats C1 (during, oos_beta): 3
- Legacy E1 beats B0 (during): 1

### 5.3 RQ2 — Calibration (EXP_002 / EXP_008)

- EXP_002 calibrated wins (moment L1): 4 / 4
- EXP_008 distribution wins: 3 / 4

### 5.4 RQ3 — Coupling (EXP_007)

- Measurable scenarios: 3 / 3

### 5.5 RQ4 — Reproducibility / significance (EXP_009)

- Reproducible rate: 1.0
- Observed MAE(E1)−MAE(B0) (OOS): -3.0297277546498247e-07
- Bootstrap CI95: [-6.125563194428647e-07, -5.842653760469078e-08]
- Diebold–Mariano stat / p: -1.534402189388721 / 0.12493073932935106

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
