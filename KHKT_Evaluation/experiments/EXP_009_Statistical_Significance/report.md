# EXP_009 — Statistical Significance & Reproducibility (RQ4)

## Protocol
oos_beta — beta=-0.0005291059078326821, lag=0, fingerprint=`1418434e9e071900` (OOS test only)

## Kết quả chính
- Observed MAE(E1)−MAE(B0): **-3.02973e-07** (âm ⇒ E1 tốt hơn)
- Bootstrap 95% CI: **[-6.125563194428647e-07, -5.842653760469078e-08]**
- Share of bootstrap diffs ≥ 0: **0.0100**
- Diebold-Mariano stat: **-1.534402189388721** (p≈0.12493073932935106)
- Reproducible rate (identical fingerprints / 5 repeats): **1.0**
- MAE(E1) variance across repeats: **0**

## Limitations
- Block bootstrap on OOS test; DM uses Newey-West with h=fwd_vol_horizon.
- Reproducibility covers deterministic offline pipeline with fixed seed.
