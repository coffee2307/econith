# EXP_007 — World Coupling Validation (RQ3)

## Mục tiêu
Đo ảnh hưởng World OFF vs ON dưới các shock: interest rate, inflation, trade.

## Kết quả
- Signal: `{'obi_column': 'indicator_obi', 'signal_source': 'sign_obi', 'used_real_obi': True}`
- Measurable scenarios: 3/3

| Scenario | Vol OFF | Vol ON | ΔVol | ΔDD | ΔSentinel | Measurable |
|---|---:|---:|---:|---:|---:|---|
| interest_rate | 0.00616397 | 0.0100653 | 0.00390136 | 0.226024 | -7 | True |
| inflation | 0.00616397 | 0.0100481 | 0.00388412 | 0.225857 | -8 | True |
| trade | 0.00616397 | 0.0101402 | 0.00397621 | 0.229528 | -8 | True |

## Limitations
- Offline shock application on returns (proxy), not live EventBus WorldKernel ticks.
- sign(indicator_obi) signal is intentionally simple; absolute DD can be extreme — use deltas.
