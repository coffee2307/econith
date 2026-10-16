# ECONITH Crypto Quant — Research-to-Paper Trading Roadmap (2026-10-16)

## Positioning and evidence
ECONITH will be an auditable, cost-aware crypto research and **paper-trading** platform.
It must not advertise guaranteed profits, universal profitability, or autonomous readiness
until net out-of-sample performance has been demonstrated independently. The bundled
`econith_quant/` trading stack contains a modified Freqtrade-like codebase; review
upstream provenance, copyright attribution, licences, and upstream security updates
before publishing releases or distributing modifications. Root MIT is **not** sufficient
to infer that all subtrees are MIT: `econith_quant/LICENSE` is GPL-3.0.

## Immediate critical fix — holdout oracle
Previously `training/deploy_gate.py` used `np.sign(forward_return_*)` as the
trade signal in the so-called holdout test. This directly reveals the future.
The activation gate now **refuses** a labeled-only holdout. A valid input must
contain (per symbol and chronological bar):
- `forward_return_<horizon>` — the realized outcome, used only for scoring;
- `oos_prediction_<horizon>` — a signed forecast from a frozen model trained
  before this out-of-sample period;
- `prediction_asof_ms` — forecast availability timestamp no later than `ts_ms`;
- `prediction_model_id` — unambiguous identifier of the frozen model.

These checks reject obvious hindsight copying and timestamp violations; they
**cannot prove** a model was actually trained in the past. Provenance must be
independently verified with train/test manifests, feature availability times,
model hashes and immutable dataset snapshots. `--skip-backtest` is rejected.
Default promotion criteria are minimum Sharpe 0.25, max drawdown 30% and 500 rows;
these are provisional safety floors, **not** proof of positive expectancy.

## Reproducible causal baseline (research only)
For a **single symbol** holdout with strictly spaced, non-overlapping candles
and matching `forward_return_<horizon>` labels, build a prediction file using
only prices at least one bar old:

```bash
python -m training.oos_baseline --input datasets/processed/quant_holdout.parquet --output datasets/processed/quant_holdout_baseline_oos.parquet --lookback 12
```

This baseline writes `oos_prediction_<horizon>`, `prediction_asof_ms` and
`prediction_model_id`. It intentionally **does not** learn a model, does not
use the future return labels for features, and does not establish profitability.
The first `lookback + 1` bars emit zero exposure. Evaluate one asset/horizon
at a time; the legacy multiasset evaluator is blocked pending time-aligned
portfolio accounting. Deployment remains blocked for heuristically estimated
live alpha, missing live marks and unverified model provenance. If the price
bar's close is not available by the preceding timestamp or venue data is stale,
the baseline timing assumptions must be revised.

## Phase A — trustworthy datasets (before new models)
1. Ingest public spot OHLCV and trades via supported exchange adapters/CCXT.
   Save venue, symbol, quote currency, market type, candle-open/close times,
   acquisition time, UTC and ingestion errors; archive raw unmodified data.
2. Universe as-of each date, including delisted markets when data is available;
   exclude inadequate notional liquidity, stale books and excessive spread.
3. Detect duplicated/out-of-order bars, corporate/token events, missing bars,
   impossible prices, clock drift and exchange-symbol mapping changes.
4. Build 1h and 4h feature/label panels with causal, availability-aware joins.
   Funding and open interest apply only to markets where legally available and relevant.

## Phase B — reproducible strategy tournament
- Baselines: cash/no-trade, buy-and-hold, moving-average momentum, volatility
  targeting, and cost-adjusted time-series momentum.
- Challengers: regularized logistic regression, gradient-boosted trees,
  calibrated probabilistic direction/return estimates, and regime-conditioned
  models. Add deep sequence/RL models **only if** simpler models are beaten.
- Chronological walk-forward: frozen train/validation/test windows; embargo
  for overlapping labels; no reshuffling time; each hyperparameter trial logged.
- Score net returns after **both-side** fees, market impact, bid/ask spread,
  borrow/funding (if applicable), price gaps, rejected orders, latency and
  liquidity caps. Stress all costs at 2x/3x plus delayed fill.
- Evaluate per venue, per asset, bull/bear/sideways regimes and by calendar
  interval. Correct for selection bias with PBO/Deflated Sharpe where feasible.
- Report CAGR/annualized return, Sharpe, Sortino, max drawdown, Calmar,
  realized PnL, exposure, turnover, trade count, hit rate and confidence intervals.
  Never treat a higher directional accuracy as sufficient for profitability.
- Portfolio backtesting must align asset returns by timestamp; concatenating
  per-symbol returns is **not** a valid multi-asset equity curve.

## Phase C — paper execution and operating controls
Keep `REALITY`/credentialed live execution disabled while validating.
Create an event-driven paper executor with event-time accounting, tick/step
rounding, min-notional rules, partial fills, duplicate-order idempotency,
disconnect/reconnect recovery and mark-to-market ledgers. Use one execution
contract in historical replay and paper mode. Enforce fail-closed pretrade
limits: no signal/no trade; max single-market allocation; total exposure
cap; per-day drawdown circuit breaker; venue status and liquidity checks;
kill switch; keys without withdrawal permissions in eventual controlled trials.
Paper performance includes actual latency and missing-data incidents.

## Phase D — release gate and governance
No live activation until: (1) complete data lineage and OOS protocol;
(2) positive robust net performance across multiple unseen regimes;
(3) anti-lookahead and survivorship tests; (4) adverse-fee sensitivity;
(5) 30–90 days of timestamped paper trades with no unexplained fills;
(6) reconciled position/fee ledgers; (7) security/license audit; and
(8) venue/legal age and eligibility checks. No universal all-token claim.
An 'all-assets' design means extensible connectors and asset selection,
not trading illiquid/unlisted markets automatically.

## Sources to reproduce and critically evaluate
- Bailey et al., *The Probability of Backtest Overfitting*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Bailey & Lopez de Prado, *The Deflated Sharpe Ratio*
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
- Easley et al. (2024), *Microstructure and Market Dynamics in Crypto Markets*
  https://stoye.economics.cornell.edu/docs/Easley_ssrn-4814346.pdf
- Bysik & Ślepaczuk (2026), *ML-Based Bitcoin Trading Under Transaction Costs*
  https://arxiv.org/abs/2606.00060
- Bui & Nguyen (2026), *AdaptiveTrend* (reported paper results; independent
  replication required) https://arxiv.org/abs/2602.11708
- Freqtrade: https://github.com/freqtrade/freqtrade
  Lookahead tool: https://www.freqtrade.io/en/stable/lookahead-analysis/
- NautilusTrader event-time replay: https://github.com/nautechsystems/nautilus_trader
- Hummingbot execution and market-making: https://github.com/hummingbot/hummingbot
- CCXT unified exchange APIs: https://github.com/ccxt/ccxt
- Binance market filters/rate limits:
  https://github.com/binance/binance-spot-api-docs
