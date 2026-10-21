# ECONITH P2-02 — Frozen 12-case profit falsification, not profit guarantee

**2026-10-21.** The exact 12 cases and old parameters were committed
**BEFORE any case in this run** in
`docs/research/P2_02_FROZEN_PAPER_MATRIX_20261021.json`.
The parameters already appeared in a *previously inspected* backtest;
thus this is a new stress challenge of the old model, not a pristine
never-seen hypothesis or prospective market trial.

Objective: publish every result, not pick the profitable symbols.

- Exact public Binance **Spot**, symbols BTCUSDT, ETHUSDT, SOLUSDT and
  DOGEUSDT (Dogecoin is NOT DOG (Bitcoin Rune)).
- Each symbol tested on 1h, 4h and 1d; 12 market/interval combinations.
  Fetch 500 latest candlesticks and reject unclosed, gaps, bad prices.
- Fixed starting **100 virtual USDT** per independent test; 24-candle
  momentum, 1% threshold, 20% max asset exposure, 15% drawdown halt.
  Only next-open historical fills with existing lot-size/min-notional rules.
- Simulate **baseline** 10bps fee + 2bps slippage + 1bps half-spread, and
  **stress x3** of each. Do not adjust this model after reading outcomes.
- Two transparent views per instrument: full recent closed history and
  **last chronological 30%**, warming signals with 25 previously closed
  candles only; tail begins with cash and fresh positions. No adaptation.
  This is NOT live forward paper trading, and the full and tail results
  overlap; likewise different cryptocurrencies are correlated.
- Compare with cash (zero return), and a fixed 20% one-shot buy-and-hold
  benchmark using the same hypothetical fees and open price; unclosed
  holdings are marked to market, not realized after exit fees.
- Include every negative, zero-fill, drawdown, missing or failed data
  case. **Failure to acquire any case sets the entire research matrix to
  INCOMPLETE**; no substituting another asset to fill the gap.
- Each source snapshot, manifest SHA-256, individual case and entire
  `unfiltered_matrix.json` is archived with short retention (public
  source only, no account/order API keys).

Negative return is **not a software test failure**. A negative result
can be the correct scientific outcome. The program only fails the CI
research integrity gate if it cannot trust its input or compute the
full predetermined comparison.

In particular, *any* positive historical x3 return does **not**
establish risk-adjusted future profit or permission to invest. No
actual trades, capital access, forecast proof, demo accounts or
production trading is enabled. After reviewing this matrix, the only
worthwhile next test of genuine predictive ability is **forward
paper running on newly arriving prices for several weeks, without
retraining on those outcomes**, collecting quote spread/market impact
and audited realized PnL. A positive result from this matrix alone
must never cause a live capital switch.

CLI: `python -m scripts.paper_unfiltered_matrix
--allow-public-market-data --simulation-only
--output-dir /ABSOLUTE/NEW/PRIVATE`. The repo's original Phase 1 score
remains **81/100 provisional OPEN**.
