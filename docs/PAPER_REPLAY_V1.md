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

## 1. Capture fully closed Binance Spot 1-hour data

```powershell
python -m econith.quant.public_binance_data --symbols BTCUSDT ETHUSDT --limit 300 --out-dir datasets/paper_spot_1h
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
19 tests passed in an isolated Python environment; this is **not** a
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
