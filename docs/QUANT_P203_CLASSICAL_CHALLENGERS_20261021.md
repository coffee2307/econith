# ECONITH P2-03 — Classical Quant Challengers, Actual Implementation

Research completion 2026-10-21: background Deep Research finished **a design
and methodology, not an implemented strategy/backtest**. The code in this
branch implements the first conservative deterministic subset and tests it.
No proprietary hedge fund signals were copied. Public conceptual sources:
Moskowitz/Ooi/Pedersen time-series momentum (JFE 2012);
Moreira/Muir volatility-managed portfolios (JF 2017);
breakout/Donchian trend-following as public systematic-trading practice;
Gatev et al. pairs mean reversion motivates investigating contrarian signals
but does **not** validate a long-only single-asset crypto adaptation.
Bailey/Lopez de Prado (PBO/DSR) warn about overfitting and repeated searches.

## Explicitly frozen before this study's public-data run

`configs/research/classical_quant_frozen_20261021.json`

No parameter fitting, optimization or post-hoc switching to a winning
asset in this experiment. The candidate set was designed **after**
prior P2-02 data had been inspected, so the exact 12 markets and recent
price history are **NOT a clean never-seen out-of-sample holdout**.
No surviving candidate can be called alpha from this study.

- **Old momentum24**: prior 24-bar >1% signal, frozen baseline
- **Trend with volatility sizing**: past 24/72 return >1%/>0,
  last close >72-bar average; volatility from 24 previous fully closed
  log-return bars, target `min(20%,20%*2.5%/realized_bar_vol)`.
- **Donchian breakout**: last 12 fully closed bars contain a close
  above the preceding 48-bar high; latest close above 48-SMA, positive
  trailing 72-bar drift; same 24-bar inverse realized-vol cap.
- **Long-only mean reversion**: prior close's z-score vs last 20 closes
  below −1.5; 96-bar return above −3% trend regime safety; 10% target.
- **Deterministic ensemble**: strictly 50% trend, 30% breakout, 20%
  mean-reversion target weight; no ML fit or LLM votes. All return 0–20%
  desired weight. Portfolio Spot long-only, no leverage or shorts.

Five strategies × BTC/ETH/SOL/DOGE × 1h/4h/1d = **60** experiment
cells. Each reports full recent history and trailing chronological
30%, with **98 preceding closed bars as warmup and NO simulated entry
during warmup**; 1x, 3x and 5x fees/spread/slippage and a cash + 20%
matched-start passive Spot benchmark. All 60 cells and three expense
scenarios are retained, including zero trades, negative returns or data
failures. Performance IS NOT a clean walk-forward ML optimization: 500
candles and historical market outcomes are too small for reliable
sample-adjusted Sharpe/PBO claims. A true prospective holdout remains
future market data and genuinely new signals.

The policy callback is isolated from market state and gets only
`tuple[Candle, ...]` of **fully closed bars preceding the fill bar**.
It cannot inspect the historical fill candle's OHLC to decide whether
to trade. The same `replay_spot` code processes every model using
next-historical-open spread/slippage/fee-adverse fills, exchange step
size and minimum notional. Every result must pass existing
`audit_paper_report` cash/position/realized PnL reconciliation.

## Real public test / CLI

```powershell
cd F:\econith-paper-mvp
# First fetch the matching PR branch into a SEPARATE worktree; do not
# overwrite the old F:\econith user checkout or untracked archives.
python -m scripts.quant_classical_challenge `
  --allow-public-market-data `
  --simulation-only `
  --output-dir "F:\ECONITH-Quant-P203-001"
```

Requires branch P2-03, not the prior P2-01 checkout. CI captures real
public Spot OHLCV with data-only API (no keys, order endpoints) and
uploads full public inputs + hashes + reports with 3-day retention.

## Promotion gate

Code CI green proves software correctness, **not profitability**. The
researcher must publish the losing challengers. Do **not** select an
asset, strategy or time frame because one previously observed sample
wins. Any improved historical result here remains exploratory until
strategy/config is frozen and genuinely prospective forward paper
data demonstrate repeatability over many independent observations.
No auto-trading, no real capital, no futures/leverage, no guarantee of
profit. Phase 1 engineering score unchanged at **81/100 provisional OPEN**.
