# EXP_007 — World Coupling Validation (RQ3)

## Mục tiêu
Đo World OFF vs World ON (không sốc) vs World ON + scenario shocks (interest / inflation / trade).
`Mt` = volatility_multiplier, `Ot` = order_flow_shock.

## Kết quả
- Signal: `{'obi_column': 'indicator_obi', 'signal_source': 'sign_obi', 'used_real_obi': True}`
- ON_no_shock measurable vs OFF: **True**
- Measurable scenarios vs OFF: 3/3
- interest_rate changes Mt/Ot vs ON_no_shock: **True**

| Case | Mt | Ot | Vol | ΔVol vs OFF | ΔMt vs ON₀ | Shock moves Mt/Ot? |
|---|---:|---:|---:|---:|---:|---|
| OFF | 1.0 | 0.0 | 0.00616397 | 0 | — | — |
| ON_no_shock | 1.8859 | -0.1376 | 0.0100251 | 0.00386111 | 0 | — |
| ON_interest_rate | 1.8891 | -0.1400 | 0.0100141 | 0.00385018 | 0.0032 | True |
| ON_inflation | 1.8850 | -0.1374 | 0.0100226 | 0.00385865 | -0.0009 | True |
| ON_trade | 1.9081 | -0.1414 | 0.0100984 | 0.00393443 | 0.0222 | True |

## Limitations
- Offline shock application on returns (proxy), not live EventBus WorldKernel ticks.
- sign(indicator_obi) signal is intentionally simple; absolute DD can be extreme — use deltas.
- World_ON_no_shock uses default_world standing coupling (no apply_scenario_shock).
- Interest-rate path requires real_rate channel in macro_to_micro so Mt/Ot move under rate shocks.
