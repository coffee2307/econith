# P2-07: choose on past evidence, execute correct target reductions

Coffee asked ECONITH to progress toward profitable trading. P2-06 supplies
forward-paper plumbing, but does not establish an economic edge. This change
focuses on strategy evaluation and execution economics, with no real orders.

## Concrete change

The historical replay previously sold only when target weight reached zero.
A change from 20% to 10% left the old larger position intact. A new opt-in
`PaperConfig(target_rebalance=True, rebalance_band=.01)` implements actual
partial sells before buys, opening-equity sizing, lot/minimum-notional checks,
and fees on each executed leg. One percentage point of equity is the fixed
turnover band; zero-target exits and over-cap reductions bypass it. Minimum
notional or quantity rounding can leave disclosed dust. Price gaps, close
marks and transaction fees can temporarily move realized weights away from
nominal targets; this is not a guaranteed continuous exposure cap.

Default historical and forward-paper behavior is unchanged: the new mode is
used only by this new offline evaluator. Existing published results remain
reproducible with the legacy default.

## Frozen experiment

- Four exact Spot pairs BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT; 1h, 4h, 1d.
- Request 1000 candles per cell; reject fewer than 800 fully closed contiguous
  candles. All 12 cells remain in the denominator on data failure or loss.
- Five existing P2-03 families, unchanged. No newly optimized parameters.
- At each boundary, use the preceding 500 closed bars only. Re-select after
  125 test bars. Preserve and evaluate the final shorter block.
- Train at assumed 3x friction: fee 30bps, slippage 6bps, half-spread 3bps.
  Require both halves of the training equity path positive, at least two sell
  legs, net return above cash and a cost-matched 20% passive position, DD below
  15%, and no halt. Rank eligible candidates by net return minus 0.5*DD.
  Ties follow the fixed registry order; CASH wins nonpositive scores.
- One continuous 100 virtual USDT book across all test blocks. No free reset
  of cash, positions, losses or drawdown at re-selection boundaries.
- Base, 3x and 5x friction reports; all signals use prior closes, simulated
  executions use the next historical open. One final predetermined liquidation
  attempt includes exit fees/slippage. Untradeable dust remains visible.
- Compare to cash, one-entry/one-exit 20% buy-and-hold, and the fixed original
  momentum rule on the exact same test timestamps, capital, lot rules and costs.
  Momentum reference uses the new target mode for execution comparability;
  it is not a rerun of the legacy P2-03 execution convention.
- Preserve the frozen plan and implementation SHA256 before the first network
  request; save original public snapshots, hash manifests, every training trial,
  full fills/equity curves and independent accounting audits.

## Run

On this branch, choose a NEW absolute output directory:

```powershell
python -m scripts.quant_cost_aware_walkforward `
  --allow-public-market-data --simulation-only `
  --output-dir "F:\ECONITH-P207-Evidence-001"
```

Offline acceptance, without third-party dependencies:

```text
python -m unittest tests.test_target_rebalance tests.test_cost_aware_walkforward -v
```

`summary.json` contains all market comparisons; `matrix.json` contains full
results; each case includes its source manifest and `walkforward.json`.
An economic loss is a successful observation, not a CI failure. Missing or
invalid data yields INCOMPLETE and a nonzero exit code, with all cases kept.

## Interpretation and limits

This is rolling historical research, not a pristine sealed holdout. The same
asset families and overlapping recent history have already informed project
work. Earlier test blocks may become later training data, as in a prospective
rolling process; they never rewrite earlier selections. No forward-return
labels are trained, so there is no overlapping supervised label to purge here.
The split training halves are stability screens, not independent validation.
Asset/timeframe cases are correlated, and their average is not a portfolio.

Assumed fees/spread/slippage are not measured historical order-book costs.
Present-day exchange lot/notional/listing rules are applied to historical bars;
point-in-time rule changes, delisted assets and survivorship are not resolved.
OHLCV does not establish liquidity, latency, market impact or actual fills.
No DSR/multiple-testing significance or future profit probability is inferred.

Even a positive result is only an exploratory candidate. `profitable_alpha_proven`
is always false and `REAL_CAPITAL_NOT_AUTHORIZED` remains in every final report.
P2-06 live forward-paper strategy remains unchanged; prospective validation of
any new policy requires a separately frozen future observation period.

## Validation status

Local red/green regressions cover missing target-reduction behavior, fees,
bands, open-gap sizing, train-only selection, future perturbations with actual
fills, full 12-case failure retention, passive entry/exit costs and preserved
evidence paths. Exact-head CI and the public 12-case economic results must be
observed before reporting acceptance or a profitable outcome.
