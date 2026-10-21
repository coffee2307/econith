# ECONITH One-Pair Paper Bot — Narrow Quant Trading MVP

**2026-10-21 · Research-only branch stacked after PR #51.**

## Exact objective

Stop expanding Phase 1 features for their own sake. This command gives a
single practical entrypoint to run the project's *already existing* public
Spot snapshot, fully closed-candle next-bar simulated fills, audited
cash/position accounting, fees/spread/slippage and cost stress. It is not
a new forecast model, continuously running forward bot, exchange testnet,
live API execution or claim of alpha.

Use an **exact Binance Spot symbol**, not just a coin name. `DOG•GO•TO•THE•MOON`
is a Bitcoin Rune (often named Dog (Bitcoin)); it is NOT DOGE. This command
intentionally never maps `DOGUSDT` to `DOGEUSDT`, and refuses if the exact
requested Binance public pair is not Spot TRADING. A different venue must be
supported with its own independently reviewed public data and real listing
rules before DOG can be paper-tested there.

## One command

From repository root using Python 3.12:

```powershell
python -m scripts.paper_trade_quickstart `
  --symbol BTCUSDT `
  --capital 100 `
  --interval 4h `
  --candles 500 `
  --allow-public-market-data `
  --simulation-only `
  --output-dir "F:\\ECONITH-Paper-Evidence\\btc-001"
```

Linux: use the same flags without PowerShell backticks and set an absolute
new output directory. Network calls are restricted to public
`data-api.binance.vision/api/v3/exchangeInfo` and `/klines`; no key,
account permission, withdrawal or order API endpoint exists.

The generated **private** directory includes a SHA-256-manifested source
snapshot, base replay with fill ledger, 3x-cost-stress replay with fill ledger,
and a compact summary. A later attempt never silently overwrites a run.
The same command on exactly `DOGUSDT` refuses if the pair is not currently
listed/trading at Binance; this does **not** imply DOG is untradable on all
other venues.

### Frozen scope

- One symbol and 300–1000 fully closed **1h, 4h or 1d** candles.
- Virtual initial cash **10 to 1,000 USDT**; only long Spot positions,
  no borrowing, no leverage, no short.
- Existing conservative momentum rule: 24 closed-candle lookback;
  entry threshold 1%, only **20% capital maximum** in this one token,
  portfolio peak-to-trough drawdown halt trigger **15%**.
- Simulated fills at **next historical candle open**, not last known
  candle close; Binance symbol minimum notional and quantity step size
  applied to every hypothetical trade.
- One base friction assumption (10bps fee, 2bps slippage, 1bps
  half spread) and one **3x** stress scenario. These are model
  assumptions, not observed market executions.
- Both accounts MUST be independently reconciled by
  `econith.quant.paper_audit`. Missing candles, gaps, malformed data,
  wrong symbol, unclosed candles, invalid capital or audit mismatch
  deny the run without an accepted result.

**All outputs label capital readiness `REAL_CAPITAL_NOT_AUTHORIZED`
even when the historical replay result is positive.** A finished historical
replay is NOT a forward-tested strategy, predictive validation or usable
live order. Prior independent ECONITH 4-hour evaluation was
`REJECTED_IN_LOCKED_TEST` under higher friction. Do not imply positive
backtest numbers will recur in meme-coin volatility.

## What comes next, not included here

A genuinely autonomous **forward paper runner** must timestamp
out-of-sample signals when they are actually created, persist next real
bid/ask observations, reconcile restart and prevent duplicate simulated
orders, watch delisting/spread/illiquidity and measure net PnL without
peeking at future n-bar outcomes. Start one pair only. Move beyond virtual
funds only after appropriate eligibility, independent prospective
performance and owner authorization; do not place live capital now.

**The frozen original Phase 1 engineering credit is not redefined by this
MVP** and stays **81/100 provisional OPEN**.
