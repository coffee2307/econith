# ECONITH — Preliminary Spot Strategy Reality Check (2026-10-16)

> Status: **NOT evidence of profitable trading. NOT an audited backtest.**
> Owner authorized only engineering/research and simulated execution.
> These calculations are a separate, read-only numerical experiment against
> Binance public OHLCV data; they have **not** been run as a full ECONITH
> FastAPI/CCXT order lifecycle test.

## Data and frozen-before-evaluation rule

At 2026-10-16 UTC, retrieved (using read-only Binance Spot public kline API):

- BTCUSDT, ETHUSDT, SOLUSDT; equal nominal max weight 20% each.
- 999 contiguous **daily** candles per asset: 2024-01-21 to 2026-10-15.
- 999 **hourly** candles per asset: 2026-09-05 00:00 UTC to
  2026-10-16 14:00 UTC (the subsequent still-open bar is excluded).
- 10,000 USDT starting **simulated** cash; no margin, leverage or shorts.
- Long if momentum from previous closed bars across prior 12 bars exceeds
  0.3%; otherwise exit. Enter and exit at the following bar's OPEN.
- Max target: 20%/asset; max combined target: 60%; once portfolio equity
  drawdown from peak reaches 15%, stop future entries and attempt exits.
- Base round-trip friction: 10 bps venue fee + 2 bps slippage +
  1 bp half-spread **per traded side** (13 bps per side).
- Stress scenario: 30 bps fee + 6 bps slippage + 3 bps half-spread
  per traded side (39 bps per side). No discount tiers/rebates.
- Asset holding & fee rounding implemented in a separate JS arithmetic
  simulation of the paper_v1 rule, not the full Python engine. Position
  minimum rules approximated with 0.00001 unit steps and $5 min notional.
  Precise venue filters, market impact and real fills have NOT been replayed.
  The empirical results should be independently reproduced from immutable
  raw Binance snapshots before any publication-grade claims.

## Results: profits can be spurious or fragile

| Period / scenario | Total net return | Simulated fills | Risk caveat |
|---|---:|---:|---|
| Daily, full 999 bars, base costs | +15.24% | 53 | Drawdown circuit breaker TRIGGERED early; long idle period |
| Daily, last 300 bars, base costs (independent restart) | +2.32% | 158 | Max DD about 14.56%; no robust excess alpha |
| Daily, last 300 bars, tripled costs | -9.55% | 88 | Drawdown circuit breaker triggered |
| Hourly, full 999 bars, base costs | -9.19% | 435 | Max DD about 13.40%; high turnover |
| Hourly, last 300 bars, base costs (independent restart) | -5.44% | 120 | Negative expectancy after assumed costs |
| Hourly, last 300 bars, tripled costs | -10.80% | 118 | Costs substantially worsen outcome |

Important: separate evaluation windows restart with fresh simulated cash and
warmup, not a single uninterrupted live trading account. Past-looking bar
selection and known strategy settings do not constitute blind walk-forward
holdouts. Comparisons across different frequency samples are illustrative
only. Circuit-breaker activation changes subsequent fills and exposure.
Therefore a more expensive-cost run can occasionally show a HIGHER return
due to a different stop time: that is path dependence, not trading skill.

## Why this fails readiness now

1. Negative ~9% on 41 days of hourly replay; over 400 order legs in a
   3-asset universe is a material cost/turnover issue.
2. A positive daily result is not independent evidence of a stable edge:
   the stop altered exposure, and Sharpe annualization of inactive periods
   is misleading.
3. This is one rule / small symbol selection / no trade-level fills,
   no uncertainty estimate or multiple-strategy correction.
4. No strategy has established independently observed, repeatable
   out-of-sample net alpha under changing volatility/spread/liquidity.
5. No proper paper/live order session with reconciled fees, positions,
   execution rejects and 30–90-day continuous soak.

## Reproduce on ECONITH branch

For hourly (up to 1000 bars, ~41 days):
```powershell
python -m econith.quant.public_binance_data --symbols BTCUSDT ETHUSDT SOLUSDT --interval 1h --limit 1000 --out-dir datasets/research_1h
python -m scripts.evaluate_spot_replay --snapshot-dir datasets/research_1h --output datasets/research_1h/analysis.json
```

For daily (up to 1000 bars, ~2.7 years):
```powershell
python -m econith.quant.public_binance_data --symbols BTCUSDT ETHUSDT SOLUSDT --interval 1d --limit 1000 --out-dir datasets/research_1d
python -m scripts.evaluate_spot_replay --snapshot-dir datasets/research_1d --output datasets/research_1d/analysis.json
```

The new evaluator reads SHA-256-verified public snapshots, compares a
no-trade cash baseline, a passive equally weighted spot hold (no stop),
and the fixed-rule strategy under zero/base/3x costs. It also generates
independently restarted 70%/30% exploratory windows. Its result always says
`NOT_READY_FOR_PROFITABILITY_OR_LIVE_EXECUTION`; short-term Sharpe is
descriptive and never a deployment gate.

This reproducible Python analysis may differ numerically from the
independent JS exploratory table due to exact lot sizes, gap alignment and
time of data retrieval. Neither is proof of future profitability.

## References / evaluation method
- Freqtrade lookahead-analysis: https://www.freqtrade.io/en/stable/lookahead-analysis/
- Bailey & López de Prado, The Deflated Sharpe Ratio (2014):
  https://doi.org/10.3905/jpm.2014.40.5.094
- Bailey et al., The Probability of Backtest Overfitting (2015):
  https://escholarship.org/uc/item/4w1110bb
