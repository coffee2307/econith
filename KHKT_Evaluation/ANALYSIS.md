# Phân tích codebase phục vụ thí nghiệm KHKT

## 1. Module có thể dùng cho thực nghiệm

| Module | Đường dẫn | Vai trò trong thí nghiệm |
|---|---|---|
| Feature store BTC | `datasets/features/BTCUSDT_features.parquet` | Market + macro đã join_asof |
| Holdout labeled | `datasets/processed/quant_holdout*.parquet` | Tập kiểm định (lưu ý macro sparse) |
| Cross-impact | `ai/simulator_engine/cross_impact.py` | `macro_to_micro` → vol/OBI/liquidity shock |
| World state | `ai/simulator_engine/macro_vectors.py` | `default_world()`, mutate macro fields |
| Calibration | `scripts/calibrate_world.py` | Moment-match OU+jump |
| Stochastic engine | `econith/world/sovereign/stochastic.py` | Simulate paths default/calibrated |
| Backtest metrics | `training/evaluation/backtest.py` | Sharpe, Sortino, MaxDD, … |
| Sentinel logic | `sentinel/manager.py` | Ngưỡng DD/VaR (proxy trong ablation) |
| Hypothesis rollouts | `data/rollouts/*.jsonl` | Log hypothesis (phụ) |
| Coeffs đã có | `models/world/stochastic_coeffs.json` | Kết quả calibrate hiện có |

## 2. Metric đã có sẵn trong codebase

**Quant / backtest:** Sharpe, Sortino, Max Drawdown, Profit Factor, Win Rate, Turnover, PnL-by-regime.

**World / coupling:** `volatility_multiplier`, `order_flow_shock`, `liquidity_drain`, `spread_widening_bps`, regime pressure.

**Calibration:** θ, μ, σ, jump_intensity/mean/std; moment empiric skew/kurt khi calibrate.

**Hypothesis:** macro deltas, `signal_score`, fingerprint (nội bộ, không phải accuracy lịch sử).

## 3. Phần cần bổ sung cho kết quả khoa học

| Khoảng trống | Cách xử lý trong KHKT_Evaluation |
|---|---|
| Chưa có protocol B0/E1/C1 | EXP_001 + EXP_004 |
| Chưa so sánh default vs calibrated moments | EXP_002 |
| Chưa ablation coupling risk | EXP_003 |
| Chưa reproducibility report | EXP_005 |
| Holdout `quant_holdout.parquet` macro gần như null | Dùng `BTCUSDT_features.parquet` (macro đầy đủ) |
| Deploy gate oracle signal | Không dùng làm metric KHKT |
| Historical event library (COVID, SVB…) | TODO — chưa có nhãn sự kiện trong repo |

## 4. Mapping RQ → Experiment

| RQ | Experiment |
|---|---|
| RQ1 — World shock có giúp đánh giá biến động hơn baseline? | EXP_001 (+ EXP_004 control) |
| RQ2 — Calibration có gần dữ liệu thật hơn? | EXP_002 |
| RQ3 — Coupling có đổi risk metrics? | EXP_003 |
| (Hỗ trợ) Tái lập | EXP_005 |
