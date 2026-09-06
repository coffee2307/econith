# EXP_009 — Statistical Significance & Reproducibility (RQ4)

## Protocol
oos_beta — beta=-0.0005085542199662192, lag=0, fingerprint=`9c78923db7400332` (OOS test only)

## Kết quả chính
- Observed MAE(E1)−MAE(B0): **-2.93328e-07** (âm ⇒ E1 tốt hơn)
- Bootstrap 95% CI: **[-5.795622591713273e-07, -3.81880053421882e-08]**
- Share of bootstrap diffs ≥ 0: **0.0100**
- Diebold-Mariano stat: **-1.531060008814809** (p≈0.1257545623315215)
- Reproducible rate (identical fingerprints / 5 repeats): **1.0**
- MAE(E1) variance across repeats: **0**

## Limitations
- Block bootstrap on OOS test; DM uses Newey-West with h=fwd_vol_horizon.
- Reproducibility covers deterministic offline pipeline with fixed seed.
