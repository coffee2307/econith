# ECONITH P2-04 — Freqtrade-inspired independent forward paper dry-run

**2026-10-21 · Draft stacked above P2-03 PR #54 · main unchanged.**

## What was learned from public projects

Freqtrade is a **bot framework**, not a profitable strategy sold as a
guarantee. Its useful patterns: dry-run forward testing, persistent
trade journals, exchange lot/min-order filters, price/fee realism, and
lookahead/recursive-bias checks. An Freqtrade-derived code tree ALREADY
exists at `econith_quant/` with bias diagnostics; do not import a second
copy or conflate the embedded GPL-licensed component with original ECONITH
code. Consider Freqtrade's GPL-3.0 if distributing derived software.

TradingAgents is a **research agent framework**, not evidence of
profitable crypto trading. Its analyst/trader/risk-review separation
inspires an independent risk veto here. P2-04 contains **no copied
TradingAgents code or LLM agent**: the currently implemented risk reviewer
is a deterministic market sanity/risk check. An LLM may only issue an
advisory hypothesis later, never approve orders or bypass data tests.

DOG•GO•TO•THE•MOON is a Bitcoin Rune memecoin, NOT a trading bot.
It is NOT DOGE. This P2-04 tests public BTCUSDT on Binance data-only
Spot REST. No DOG or DOGE proxy substitution.

## Implemented: real new information, no backdated fills

`econith.quant.forward_quote_dryrun` performs independent explicit steps:

1. `init`: new private SQLite evidence database; immutable
   virtual 10–1000 USDT research account; a strategy name and exact Spot
   symbol/timeframe are fixed forever.
2. `observe`: public *fully closed* OHLCV via pinned
   `data-api.binance.vision`. A closed-bar-only deterministic model
   records one decision **at its actual observation time**, not the
   historical candle close. It cannot issue orders, and awaiting signal
   survives process restart. Previously recorded source OHLC is compared
   byte-equivalently for overlap, otherwise fail-closed; skipping new
   closed candles also halts instead of retroactively claiming profits.
3. `fill`: a later independent public **bookTicker bid/ask observation**,
   after a recorded decision and within 60 seconds. Strict exact-symbol
   quote validation; reject stale/local timestamp reversal, crossed
   spread, spread above 100 bps, mark deviations above 20%, invalid
   exchange lot/min-notional/cash, and already consumed decisions. The
   current quote is marked on the bid side; paper fills on ask for a
   BUY / bid for a SELL plus adverse assumed slippage and 10bps fees.
   One SQLite IMMEDIATE transaction persists receipt, cash, quantity,
   and consumed signal. Repeated `fill` cannot duplicate it.
4. `status`: read current virtual cash/position and ledger progress.
   Default signal policy is the previously frozen trend-vol candidate;
   not a claim of alpha.

Market analyst (deterministic signal) => independent risk gate =>
paper-bookkeeper. The quote endpoint is GET-only and limited to the
public data-only Binance host. No API keys, orders, withdrawal, margin,
futures, leverage or real-capital endpoints are available.

### Run on a new isolated worktree containing this PR

```powershell
cd F:\econith-paper-p204
python -m econith.quant.forward_quote_dryrun --action init --ledger "F:\ECONITH-Paper-P204.sqlite" --symbol BTCUSDT --interval 1h --strategy trend_vol_v1 --capital 100 --simulation-only
python -m econith.quant.forward_quote_dryrun --action observe --ledger "F:\ECONITH-Paper-P204.sqlite" --allow-public-market-data --simulation-only
python -m econith.quant.forward_quote_dryrun --action fill --ledger "F:\ECONITH-Paper-P204.sqlite" --allow-public-market-data --simulation-only
python -m econith.quant.forward_quote_dryrun --action status --ledger "F:\ECONITH-Paper-P204.sqlite" --simulation-only
```

All commands are **separate**; the operator must capture a *newer* bid/ask
quote within 60s after the actual observed decision. If quote is stale,
the signal remains pending; do not pretend it filled. An expired quote
requires operator inspection; no silent repair or automatic restart.
After a successful fill, new signals require one NEW genuinely closed
source candle. Polling before a new candle must be idempotent.

## What this does not prove

- A public `bookTicker` REST quote has **no independent exchange
  timestamp**. `recorded_local_ms` and `received_quote_ms` are local
  clock observations, **not** independently signed exchange receipts.
- A paper fill at observed bid/ask is a hypothetical immediate taker
  fill, not guaranteed executable volume. Orderbook depth, partial
  fills, orders-in-flight, venue outages, funding/liquidity shocks and
  market impact remain unverified.
- One finite CI cycle ≠ 24h/30d continuous prospective PnL; this code
  provides manual step operations, not an always-on daemon or profit
  forecast. There is no TradingAgents LLM ensemble and no Freqtrade
  hyperoptimization integrated into this subsystem.
- Trade journal remains local SQLite; local file permissions and
  backup/restore remain operator responsibilities. Historic code
  backtests may be profitable even when forward decisions lose.
- No DOG Rune strategy or venue-specific order adapter was implemented.

Next scientific step: run this **simulation-only** with fixed policies
on newly arriving candles, collect independent bid/ask and virtual fill
quality for many days, compare net after-cost PnL against cash and
20%-capital buy-and-hold. Software correctness or one paper fill is NOT
a performance or real-money promotion criterion. Institutional Phase 1
score remains **81/100 provisional OPEN**.
