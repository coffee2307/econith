# EXP_006 — Historical Event Replay (RQ1)

## Mục tiêu
So sánh B0/E1/C1 trong cửa sổ **pre / during / post** sự kiện lịch sử, với β OOS fit toàn cục (không refit trong event).

## Protocol
- oos_beta global: beta=-0.0010569172834840248, lag=5, fingerprint=`d170c72b5758c87e`
- Legacy multiplicative during-window wins E1>B0: 1

## Tóm tắt (during)
- Events OK: 4/4
- E1 beats B0: 2
- E1 beats C1 (random control): 3

| Event | Phase | B0 MAE | E1 MAE | C1 MAE | Δvol E1 | RegDir E1 | E1>B0 | E1>C1 |
|---|---|---:|---:|---:|---:|---:|---|---|
| fed_hike_cycle_2022h2 | pre | 0.0026884 | 0.0026886 | 0.0026904 | 0.00022115 | 0.3905 | False | True |
| fed_hike_cycle_2022h2 | during | 0.0029179 | 0.002918 | 0.002918 | 0.00020857 | 0.3639 | False | True |
| fed_hike_cycle_2022h2 | post | 0.0020911 | 0.0020908 | 0.0020908 | 0.00014398 | 0.3368 | True | False |
| svb_banking_stress_2023q1 | pre | 0.0023616 | 0.0023615 | 0.0023614 | 0.00019045 | 0.3636 | True | False |
| svb_banking_stress_2023q1 | during | 0.003976 | 0.0039756 | 0.0039757 | 0.00026295 | 0.3704 | True | True |
| svb_banking_stress_2023q1 | post | 0.00094517 | 0.00094691 | 0.00094585 | 7.9631e-05 | 0.3472 | False | False |
| fed_peak_hold_2023h2 | pre | 0.0012 | 0.0011968 | 0.0011993 | 9.9553e-05 | 0.3756 | True | True |
| fed_peak_hold_2023h2 | during | 0.0010629 | 0.0010634 | 0.001063 | 8.0549e-05 | 0.3749 | False | False |
| fed_peak_hold_2023h2 | post | 0.0017846 | 0.0017794 | 0.0017836 | 0.00014262 | 0.3711 | True | True |
| yen_carry_vol_2024aug | pre | 0.00098215 | 0.00098165 | 0.00098386 | 9.7129e-05 | 0.3398 | True | True |
| yen_carry_vol_2024aug | during | 0.0027975 | 0.0027954 | 0.0027964 | 0.00019854 | 0.3380 | True | True |
| yen_carry_vol_2024aug | post | 0.0011638 | 0.0011632 | 0.001164 | 0.00010811 | 0.3681 | True | True |

## Limitations
- Beta fit once on global train; event scores use fixed beta (no per-event refit).
- Event labels are calendar windows; not exchange-official tags.
- Only events overlapping BTC feature span (~2022-04 to 2026-07) are included.
- Offline World overlay proxy — not live EventBus SIMULATION.
