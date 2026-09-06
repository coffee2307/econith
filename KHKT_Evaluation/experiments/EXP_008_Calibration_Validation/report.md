# EXP_008 — Calibration Validation (RQ2)

## Mục tiêu
So sánh phân phối / moment: **Real data** vs **Default sim** vs **Calibrated sim**.

## Kết quả
- Features plotted: 4
- Calibrated wins (lower moment L1): 3

Biểu đồ: `results/plots/dist_*.png` (Before/After/Real overlay).

## Limitations
- Single-feature OU+jump paths — not full multi-agent World dynamics.
- Distribution distance reported via moment L1 (mean/std/skew), not full KS/Wasserstein (optional TODO).
