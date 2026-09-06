# Dataset Description — ECONITH KHKT Evaluation

## 1. Primary market–macro panel

| Field | Value |
|---|---|
| Path | `datasets/features/BTCUSDT_features.parquet` |
| Asset | BTCUSDT |
| Approx. span | 2022-04-15 → 2026-07-25 (UTC, from `ts_ms`) |
| Construction | Feature pipeline `join_asof` (market HF + macro + tradfi), no look-ahead |
| Fabricated? | **No** |

### Key columns used in experiments

- Market: `price`, **`indicator_obi`** (canonical OBI; also accept alias `obi` if present), `ts_ms`
- RQ3 position signal: `sign(indicator_obi)` via `data_loader.position_signal_from_panel` — metrics must record `signal.used_real_obi`; never claim OBI after silent return-sign fallback
- Macro (time-varying): `macro_vix`, `macro_yield_spread_10y_2y`, `macro_hy_oas_spread`, `macro_fed_funds_effective_rate`, `macro_treasury_10y_yield`, `macro_real_gdp_growth`, `macro_unemployment_rate`

## 2. Calibration coefficients

| Field | Value |
|---|---|
| Path | `models/world/stochastic_coeffs.json` |
| Method | Moment-matching OU + jump (`scripts/calibrate_world.py`) |
| Fabricated? | **No** (artifact already present in repo) |

## 3. Datasets intentionally not used as primary KHKT evidence

| Path | Reason |
|---|---|
| `datasets/processed/quant_holdout.parquet` | Macro columns mostly null/constant in practice |
| Synthetic panel inside calibrator fallback | Only used if real macro missing — experiments prefer real BTC features |

## 4. Event windows (EXP_006)

Documented in `KHKT_Evaluation/common/event_library.py`. Windows are real calendar intervals overlapping the BTC feature span. They are **research labels**, not exchange-certified event feeds.

## 5. Provenance rule

Every experiment payload includes `"fabricated": false` when completed on real data, or `status: blocked` with TODO when data is insufficient. No synthetic numbers are written into reported metrics tables.
