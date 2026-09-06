# EXP_003 — Coupling ablation (RQ3)

## Mục tiêu
Cùng một kịch bản thị trường: coupling OFF vs ON có tạo khác biệt đo được trên risk metrics?

## Kết quả
- Signal: `{'obi_column': 'indicator_obi', 'signal_source': 'sign_obi', 'used_real_obi': True}`
- Measurable difference: **True**
- max_drawdown Δ (B−A): 0.191239
- portfolio_vol Δ: 0.0041854
- sentinel proxy Δ: -71

Shock: `{'volatility_multiplier': 1.8737, 'order_flow_shock': -0.1285, 'liquidity_drain': 0.1465, 'spread_widening_bps': 5.27, 'headline': 'Macro coupling: tension 3%, unrest 0.20 -> vol x1.87, OBI bias -0.13, liquidity drain 15%'}`

## Giới hạn
Offline proxy của coupling (không phải full EventBus SIMULATION runtime).
Chỉ mô tả `sign(OBI)` khi `used_real_obi=true` (cột `indicator_obi`).
