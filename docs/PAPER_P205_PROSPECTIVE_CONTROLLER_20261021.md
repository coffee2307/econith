# ECONITH P2-05 — Real new public quotes, finite paper controller and PnL

2026-10-21. This research step uses public capabilities from:
- Freqtrade: persistent dry-run, protections, lookahead testing and
  separation of backtest from exchange-observed paper trading.
- Hummingbot: explicit controller / executable lifecycle separation,
  standardized market input and auditable finite execution outcomes.
- TradingAgents: analyst, risk manager and portfolio review *roles* only.
  No LLM is authorized to override risk gates. No repository copied.

This is original Python that invokes only ECONITH's existing read-only
public Binance data-only Spot APIs. Freqtrade code already exists in
`econith_quant`; its license remains applicable to that separate tree.

## Included

`econith.quant.paper_forward_monitor.append_mark` records each
actually RECEIVED public bid/ask quote, virtual cash/position/equity
marked at bid, observed drawdown, first-quote 20% buy-and-hold
benchmark (same simulated ask/slippage/fee + venue minimums), and
cash control 0%. An append-only monotonically increasing hash-linked
SQLite journal is reverified on EVERY report; chain hashes are
LOCAL tamper detection and not external cryptographic attestations.
A 15% marked drawdown triggers a persistent veto for new entries.
No trading order or account access ever exists.

`scripts.paper_forward_cycle` offers `--mode once`, `--mode report`,
and optional explicitly foreground `--mode bounded-watch` (60-3600s
polling and at most 48 cycles). Each new-bar cycle obtains a real quote
for monitoring, observes a FULLY closed recent public candle, persists
a strategy target at actual observation time, requests a SEPARATE
LATER bookTicker quote to model a taker fill and uses a THIRD quote to
value the resulting virtual book. No synthetic historical trade,
trade on unclosed candle, book spread bypass or future-price peek.
On restart, a previously persisted pending signal can only be
resumed inside its original <=60-second window; after expiry the
system refuses operation pending human review. Repeated same
closed candle results in a fresh market mark but NO repeat trade.

All loss and win observations survive; the report does NOT declare
future alpha regardless of headline percentage. Holding a virtual
position without a fresh mark is labelled STALE, not a realized PnL.

## Run on a NEW isolated PR worktree (never overwrite F:\econith)

First, initialize a NEW virtual ledger:

```powershell
cd F:\econith-paper-p205
python -m econith.quant.forward_quote_dryrun --action init --ledger "F:\ECONITH-Paper-P205.sqlite" --symbol BTCUSDT --strategy trend_vol_v1 --interval 1h --capital 100 --simulation-only
```

Then **each iteration** is one finite command:
```powershell
python -m scripts.paper_forward_cycle --mode once --ledger "F:\ECONITH-Paper-P205.sqlite" --allow-public-market-data --simulation-only
python -m scripts.paper_forward_cycle --mode report --ledger "F:\ECONITH-Paper-P205.sqlite" --simulation-only
```

Optional finite foreground watch (requires terminal to remain active):
```powershell
python -m scripts.paper_forward_cycle --mode bounded-watch --poll-seconds 3600 --max-cycles 24 --acknowledge-foreground-only --ledger "F:\ECONITH-Paper-P205.sqlite" --allow-public-market-data --simulation-only
```

The research journal survives process restart, not power loss of the
underlying disk. Unlike a hosted daemon, local foreground watch STOPS
when terminal or computer sleeps. This is NOT proof of uninterrupted
24h market ingress.

## Important limitations / no real-money readiness

One quote does not establish exchange execution. Binance bookTicker
does not expose an independently verified trade-execution timestamp.
Local receipt timestamps and SQLite SHA-256 links are NOT signed by
the exchange. No orderbook depth, partial fills, maker/taker volume
capacity, venue slippage observations, taxes, funding, withdrawals,
trading credentials, actual positions or profit guarantees.

If adding this monitor to an OLD P2-04 journal that already holds a
position, the passive comparison begins at the FIRST subsequent quote
rather than at strategy inception. The report flags
`starting_with_preexisting_virtual_position=true` so returns cannot
be interpreted as a valid same-start causal strategy-versus-passive
comparison. For honest same-start results, initialize a NEW ledger.

No automatic token substitution; BTC/USDT Spot only in CI, DOG Rune
is not DOGE. No real orders, no investment authorization, no leverage.
Historical Phase 1 remains 81/100 provisional open. Current
strategies remain exploratory with no demonstrated forward edge.
