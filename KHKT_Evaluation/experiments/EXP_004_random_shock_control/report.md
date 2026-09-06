# EXP_004 — Random shock control

## Mục tiêu
Kiểm tra E1 (World structured) có tốt hơn nhiễu ngẫu nhiên (C1) trên MAE/RMSE không.

## Kết quả
- E1 better than B0 (MAE): False
- E1 better than C1 (MAE): False
- Passes random control: **False**
- vol_mult unique≈510

| Model | MAE | RMSE | DirAcc |
|---|---:|---:|---:|
| B0 | 0.0015783 | 0.00213023 | 0.3450 |
| E1 | 0.00157929 | 0.00213127 | 0.3455 |
| C1 | 0.00157818 | 0.0021304 | 0.3712 |
