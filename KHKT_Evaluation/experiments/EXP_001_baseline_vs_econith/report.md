# EXP_001 — Baseline vs ECONITH vs Random Control (RQ1)

## Mục tiêu
Đánh giá liệu context World (`macro_to_micro`) có cải thiện dự báo biến động so với market-only và so với random control.

## Protocol
- Primary: **oos_beta** (train_frac=0.7, beta=0.006742032115914304, lag=0, fingerprint=`e2bc3fe0db72a5bd`)
- Metrics reported on **test holdout only** (n_test=8940).
- Legacy multiplicative retained under `protocol_legacy` (test-mask contrast: E1>B0=False, pass_C1=False).

## Dataset
`F:\econith\datasets\features\BTCUSDT_features.parquet` — rows=30000

## Kết quả (OOS test)

| Model | MAE | RMSE | Direction Accuracy |
|------|-----|------|--------------------|
| B0 | 0.0015783 | 0.00213023 | 0.3450 |
| E1 | 0.00158375 | 0.00213605 | 0.3463 |
| C1 | 0.00158028 | 0.00213158 | 0.4268 |


- MAE improvement E1 vs B0 (%): **-0.3457353103237798**
- Passes random control (E1 MAE < C1): **False**
- Δvol MAE E1: **0.00013414104300944827**
- Regime DirAcc E1: **0.34634746615952566**
- vol_mult unique≈506, std=0.02162573537205665

## Limitations
Offline proxy; không phải full EventBus live coupling. Beta không được tune trên test.
