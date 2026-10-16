# ECONITH Quant — Read-only Binance capture and deterministic paper replay

Status: **research-only vertical slice**. This is not a production bot, not a
live-exchange paper session, not a profitable-strategy claim and not
`READY FOR DEMO TRADING`. It is a reproducible no-credential replay.

## Requirements
- Python 3.11+; no third-party dependencies for the new two modules.
- Repository root as working directory.
- Public Binance Spot market-data access available from your device (venue
  access and availability depend on jurisdiction/network).
- No API key, order API, Futures, Margin or leverage is used.

## 1. Capture fully closed Binance Spot 1-hour or 1-day data

```powershell
python -m econith.quant.public_binance_data --symbols BTCUSDT ETHUSDT --interval 1h --limit 300 --out-dir datasets/paper_spot_1h
```

The collector calls public `exchangeInfo` and `klines`, parses lot size and
minimum notional for each symbol, rejects broken candles/clock gaps, aligns
assets on a common UTC timeline, excludes still-open candles, and writes:
- `market.json`: timestamped raw Binance OHLCV kline arrays
- `rules.json`: observed Spot quantity step and minimum notional
- `manifest.json`: source, UTC as-of timestamp, symbol set and SHA-256 hashes

The preferred `--snapshot-dir` replay CLI checks SHA-256 hashes, source,
symbol consistency and time cutoff before reading any snapshot. Preserve
the original files immutable and make a separate directory for each snapshot.
Captures are **historical extracts**, not WebSocket streams. Max 1000 hourly
candles per symbol/call. This does not handle backfill pagination/delistings.

## 2. Replay spot orders without a trading API

Replay using the automatically verified SHA-256 manifest. This is the
preferred path; it rejects any tampered raw data or mismatched symbols.

```powershell
python -m econith.quant.paper_v1 --snapshot-dir datasets/paper_spot_1h --output datasets/paper_spot_1h/report.json
```

The legacy manual `--input` / `--rules` / `--asof-ms` flags remain available
for controlled experiments, but do not perform manifest verification.

`report.json` contains the unified portfolio equity curve, cash, positions,
simulation fills, rejection reasons, realized transaction fees included in
capital, maximum drawdown and net total return. All fills have
`execution_mode=SIMULATION`; no real order is ever sent.

### Execution timing and safety
- Signals are generated using only earlier closed bars.
- Decisions are evaluated at the next bar and fills are priced at its OPEN,
  with unfavorable spread/slippage and an explicit commission.
- The default deterministic baseline buys on positive 12-hour momentum above
  0.3%, otherwise exits. This is an **untuned hypothesis**, not demonstrated
  alpha. Other strategies/models are not yet routed through this module.
- Max requested allocation: 20% per asset, total requested 60%; with no
  borrowing/short selling. Market price moves can cause drift above these
  soft entry-time limits; continuous rebalancing is not yet implemented.
- 15% drawdown triggers a halt of subsequent entries and exit attempts at the
  next bar open (unless minimum-notional dust prevents exiting). It is not a
  guaranteed protective stop; there is no intrabar order-book modeling.
- Buy/sell fees are charged, but actual venue commission tiers, rebates,
  funding, latency, partial fills and market impact are not observed. These
  must be modeled and validated before reliance.
- Missing, unclosed or non-contiguous candles, misaligned markets and
  unsupported Spot filter responses cause explicit errors, not fabricated data.

## Test and measured scope

```powershell
python -m pytest tests/test_paper_v1.py tests/test_public_binance_data.py tests/test_paper_binance_sample.py -q
```

Verified independently against the exact Git blob hash of
`econith/quant/paper_v1.py` and `tests/test_paper_v1.py` for this PR.
20 in-memory no-fixture checks passed on an authorized Python device; full pytest/CI is still pending; this is **not** a
claim of full repository test coverage. The historical Binance-format
fixture is eight BTCUSDT hourly candles retrieved read-only on 2026-10-16.
They prove parsing and deterministic execution wiring, not statistical edge.

## What remains before demo readiness
- Streaming market-data adapter and incremental/backfill storage.
- Integrated API and real dashboard Start/Stop controls; this CLI does not
  connect to the existing Next.js frontend.
- Persisted paper event journal, order IDs, idempotency and crash recovery.
- Reconciled Realized PnL + reward ledger and guarded $100 reproduction rule.
- Robust time-aligned multi-asset research, walk-forward AI evaluation,
  statistically adequate multi-regime samples, transaction cost calibration.
- Real exchange rule details: quantity/price filters, notional limits, outages,
  authentication governance, slippage and partial-fill modeling.
- Verified full CI and GPL-compatible review of bundled trading source tree.

References: [Freqtrade lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/),
[NautilusTrader](https://github.com/nautechsystems/nautilus_trader),
[CCXT](https://github.com/ccxt/ccxt),
[Qlib](https://github.com/microsoft/qlib).

No real trading may be enabled as a side effect of these research commands.


## Longer-horizon costs and baseline comparison

Do not equate a profitable short sample with a usable trading bot.
The current collector also supports `--interval 1d --limit 1000`
for approximately 2.7 years of daily candles. Before trying new model
complexity, compare the fixed-rule strategy to cash and passive Spot
holdings, then run fee/spread/slippage scenarios:

```powershell
python -m econith.quant.public_binance_data --symbols BTCUSDT ETHUSDT SOLUSDT --interval 1d --limit 1000 --out-dir datasets/research_1d
python -m scripts.evaluate_spot_replay --snapshot-dir datasets/research_1d --output datasets/research_1d/analysis.json
```

The evaluator does not label a short study as demonstrated OOS alpha or
`READY FOR TRADING`; outputs the explicit
`NOT_READY_FOR_PROFITABILITY_OR_LIVE_EXECUTION` status.

**Preliminary empirical findings (separately calculated):**
- 999 daily BTC/ETH/SOL candles (2024-01-21 through 2026-10-15),
  base-cost momentum: approximately **+15.24%**, but the drawdown
  breaker halted the strategy early.
- 999 hourly candles (2026-09-05 through 2026-10-16),
  base-cost momentum: approximately **-9.19%**, with 435 synthetic fills.
- Costs increased 3× worsened the independently restarted final 300-hour
  return from about **-5.44%** to **-10.80%**.
These are **not** certified full-engine, out-of-sample or live results.
See `docs/PRELIMINARY_CRYPTO_BACKTEST_20261016.md`.

## Mainnet execution block

The legacy CCXT connector previously fell back to fabricated synthetic fills
while labelled `REALITY` when the exchange connection was unavailable.
On this research branch, `REALITY` without authenticated **demo** connectivity
rejects orders; only explicit `SIMULATION` can create synthetic fills.
Capital-bearing mainnet connectivity is hard-disabled independently of
credentials. This is a research-stage safety barrier, not evidence of
operational readiness.
