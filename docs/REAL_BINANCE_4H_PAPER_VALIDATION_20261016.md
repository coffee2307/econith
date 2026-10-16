# ECONITH Quant — Binance 4h paper-capital sweep and chronology gate (2026-10-16)

## Verdict: NOT READY FOR HIGH-EFFICIENCY DEMO OR ANY REAL CAPITAL

This investigation ran public Binance Spot 4-hour BTCUSDT, ETHUSDT and
SOLUSDT candles with an offline, **actual ECONITH Python implementation**
in memory on the user's authorized Desktop Commander device. No
Binance account was connected, no API keys were read, no real/testnet
orders were placed, no assets were moved and no project folder was modified.

The evidence below checks accounting and strategy fragility, not the
profitability of a deployed bot.

## Market sample and cost assumptions

- Binance public read-only Spot market records: **999 complete 4h bars per
  symbol**, 2026-05-03 00:00 UTC to 2026-10-16 08:00 UTC.
- Venue's observed order minimum NOTIONAL: 5 USDT for these symbols;
  LOT_SIZE quantity steps: BTC .00001, ETH .0001, SOL .001.
  Market conditions, fees and constraints change; refresh before each run.
- V1 reference rule: 12 past fully closed 4h bars' momentum > 0.3%,
  next-bar open simulated entry, long-only, 20% weight per asset and
  60% maximum desired total weight, 15% permanent drawdown entry halt.
- Hypothetical friction BASE: 10bps fee, 2bps slippage, 1bp
  half-spread per side (13bps total/side).
- Hypothetical STRESS_3X: 30bps fee, 6bps slippage, 3bps
  half-spread per side (39bps total/side).
- Simulated starting cash tested **10, 100 and 1,000 USDT**.
  The 10-USDT portfolio cannot trade at 20% maximum per asset
  because its desired order (~2 USDT) is below 5-USDT minimum.

## Full 999-bar 4h Python execution

| Capital (USDT) | Base net result | Stressed net result | Base order legs | Base explicit fees |
|---:|---:|---:|---:|---:|
| 10 | 0.00% | 0.00% | 0 | 0.00 |
| 100 | +1.74% | -13.71% | 376 | 7.56 |
| 1,000 | +1.66% | -13.56% | 376 | 76.11 |

Every case returned `SIMULATED_LEDGER_RECONCILED`: cash, execution
prices and fees, position balances, total net realized/unrealized
portfolio PnL and reported drawdown matched. No external exchange
execution was verified. The large capital turnover/fees make the
existing high-frequency variant economically fragile.

## Chronologically held-out candidate tournament on 4h data

Three **predeclared** fixed hypotheses; train-only selection
using 70% of the 4h bars (699 bars), then one 30% test period
(300 bars, restarted account and new warm-up):

| Candidate ID | Train base return | Train 3x return | Train max DD | Train score |
|---|---:|---:|---:|---:|
| momentum_12_30bps | +9.39% | -13.56% | 6.96% | -17.04% |
| momentum_24_100bps | +8.88% | -13.97% | 9.45% | -18.70% |
| momentum_48_200bps | +10.54% | +6.10% | 5.87% | +3.17% |

The selection score is `min(net_train_base, net_train_stress)
- 0.5 * train_max_drawdown`; the no-trade CASH strategy
always beats non-positive scores. **Selected:** momentum_48_200bps.

Locked 300-bar test of that one training-selected candidate:

- Base assumed friction net return: **+0.15087%**.
- Triple-friction net return: **-2.62787%**.
- Max base drawdown: **3.19533%**.
- Base synthetic order legs: **54**.
- Base and stress audits: both cash/position ledgers reconcile.
- Historical block-bootstrap one-sided 90%-lower total return
  estimate: **-5.08956%** (descriptive conditional calculation only).
- Decision: `REJECTED_IN_LOCKED_TEST` because stress and
  lower-bound outcomes fail; **no demo/live activation**.

**Methodological limitation:** the 4h market history and earlier
12-bar strategy results had already been inspected during development.
Although the program itself selects using only the first 70% of
bars (this invariance is regression-tested), this 30% window is not
a fully pristine research-wide blind holdout. It should NOT
be presented as statistically independent validation of alpha.
Block bootstrap does NOT give a literal 90% probability of future profit.
Use later, newly arriving forward paper results for genuinely prospective
validation, and register candidate definitions before these data arrive.

## Reproducible commands (Windows, from ECONITH repository root)

No account credentials, no order endpoints:

```powershell
python -m econith.quant.public_binance_data --symbols BTCUSDT ETHUSDT SOLUSDT --interval 4h --limit 1000 --out-dir datasets/quant_4h
python -m scripts.paper_capital_sweep --snapshot-dir datasets/quant_4h --output datasets/quant_4h/capital_sweep.json
python -m scripts.walkforward_spot_tournament --snapshot-dir datasets/quant_4h --capital 1000 --output datasets/quant_4h/tournament.json
```

The first command writes SHA-256 hashes and venue-rule snapshots. The
second reconstructs cash, weighted-average cost basis, realized and
unrealized PnL from simulated fills and guards against forged cash/profit
reports. The third rejects training candidates lacking positive
cost-stressed risk-adjusted scores and runs a held-out period only
for the one selected candidate. Neither command enables live orders.

Note: The connected desktop lacks approved access to `F:/econith`,
so the **file-based CLI commands above have not run** there.
The actual code was loaded from GitHub directly into Python memory,
and 999-bar live-data replays executed without touching the filesystem.
CI remains blocked with zero jobs; no full repository testing passed.


## Predeclared universe robustness (same locked test)

The *same* frozen three-candidate tournament was independently rerun on
alternative pre-defined symbol groups (each starts with fresh 1,000 USDT):

| Universe | Train selection | Test base return | Test 3x return | Verdict |
|---|---|---:|---:|---|
| BTC + ETH | momentum_48_200bps | -1.07804% | -2.80913% | REJECTED |
| ETH + SOL | momentum_48_200bps | +0.25087% | -2.02028% | REJECTED |
| BTC alone | CASH | Not traded | Not traded | No candidate passed train gate |

Changes to the traded asset universe do **not** rescue the model under cost
stress. The results were obtained from the same already-inspected historical
window; they are robustness diagnostics, not independent OOS proof.

## Test evidence in this continuation

- The **latest fetched GitHub source** for paper replay, data collector,
  ledger audit, capital sweep and tournament was loaded into Python on
  the connected Windows device. 29 zero-fixture source-level regression
  tests passed (0 failed). Separately, the parametric 10/100/1000-USDT
  ledger cases passed.
- The full pytest suite (including tmp_path fixtures), GitHub Actions CI,
  Next.js build and FastAPI/CCXT E2E were NOT run successfully.
- Real 999-bar Binance market data was passed into the actual Python
  `sweep_capitals` and `tournament` functions. All six simulation cash
  and PnL ledgers reconciled. This is **not a real exchange/demo order
  submission** and should never be reported as such.

## Next release gates

1. Repair GitHub Actions and rerun full Python/TypeScript tests.
2. Freeze candidate and data provenance, run multiple independent
   chronological walk-forward folds, test pre-registered prospective data.
3. Reduce unnecessary turnover **based only on training folds**,
   not the 300-bar test already opened; integrate depth/impact/real
   maker/taker fees and min size.
4. Persist replay trade journal atomically; recover/reconcile exactly
   after crashes, duplicate intents and simulated partial fills.
5. Connect one-click backend/UI to a separate **paper-only** operation
   that never routes to mainnet credentials.
6. Only after strong *prospective* net alpha and 30–90 days of
   timestamped paper execution consider opening **exchange demo/testnet**
   order submissions. Do not open live positions.
7. A 100-USD bot reproduction eligibility metric must require
   externally reconciled **net realized** PnL, not equity marks,
   synthetic closed fills or historical backtest scores.

Current decision: `NOT_READY_FOR_CAPITAL_OR_AUTONOMOUS_DEMO`.
