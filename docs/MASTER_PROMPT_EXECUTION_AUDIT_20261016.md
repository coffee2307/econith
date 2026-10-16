# ECONITH Quant — Master Prompt Phase Audit / 2026-10-16

**Scope:** 17-section Master Prompt supplied by owner. This is an honest
incremental engineering audit, NOT a declaration that any untested feature is
complete. Branch: `fix/fail-closed-oos-deploy-gate-20261016`, PR #21
remains draft. Research and simulation only; no capital-bearing orders.

## Phase 0: Repository observations (reviewed source files, not a full security audit)

| Area | Existing code | Initial decision |
|---|---|---|
| Dashboard | `dashboard/`, Next.js/TypeScript | Keep; audit UX/action->API wiring before redesign |
| Runtime | `main.py`, `core/event_bus.py`, `bridges/quant_bridge.py` | Keep event bus; isolate any capital-bearing bridge |
| Exchange | `quant/ccxt_bridge.py`, `infrastructure/websocket/streamer.py` | Keep as candidates; verify source provenance and no fake fills |
| Risk | `sentinel/`, `quant/pretrade_gate.py` | Keep interfaces; estimated confidence is NOT validated edge |
| Research | `training/`, `econith/quant/`, `KHKT_Evaluation/` | Fix oracle holdout and portfolio timing first |
| Packaged bot tree | `econith_quant/` | GPL-3.0 subtree; upstream provenance/legal review required |
| World | `econith/world/` and simulation | Keep OFF by default; do not couple synthetic macro values to trading |
| Infra | Docker Compose, PostgreSQL, Redis | Keep; test startup/recovery/state idempotency separately |

Key original release-gate defect: `training/deploy_gate.py` derived
supposed holdout signals from the sign of the **realized forward return**.
PR #21 blocks that look-ahead oracle and makes unverifiable model promotion
fail closed. Raw model name/timestamp claims still require independent
data lineage and actual walk-forward training.

Original multi-symbol evaluator concatenated per-symbol returns rather
than aligning them into one cash/asset portfolio. PR #21 blocks this
invalid use of that legacy evaluator and introduces a separate,
synchronized paper spot replay. No automatic migration of existing data,
API or UI is claimed.

## Phase 1: Method screening

- **Baseline first:** lagged time-series momentum with no future candle
  information, and cash/no-trade. Claim: implemented as a benchmark;
  NOT proven profitable.
- **Forecasting:** logistic/boosted-tree models with calibrated
  probabilities, chronological purged splits and frozen OOS predictions
  are preferred next over expensive RL or agent swarms.
- **Regimes:** validate a simple volatility/trend filter before fitting
  HMM/multi-agent adaptive control.
- **RL/evolution:** prohibit self-promotion; OOS holdouts, transaction-cost
  sensitivity and paper-ledger truth are prerequisites. New agent children
  would enter candidate/research status only.
- Research references: Freqtrade lookahead validation,
  Qlib quantitative workflows, NautilusTrader event-driven simulator,
  Hummingbot execution architecture, Bailey et al. backtest-overfitting
  literature, CCXT market adapters. Source links in docs/PAPER_REPLAY_V1.md
  and docs/TRADING_RESEARCH_ROADMAP_2026.md.

## Phase status against supplied Master Prompt

| Phase | Honest completion status | Verified scope and blocker |
|---|---|---|
| 0 - Audit | PARTIAL | Representative source & design risks inspected; full code/dependency/security inventory missing |
| 1 - Research | PARTIAL | Candidate stack and references evaluated; no systematic controlled comparison yet |
| 2 - Intelligent Quant Engine | IN PROGRESS | Public Binance hourly collector, causal momentum benchmark, next-open multiasset paper replay; no integrated strategy registry/AI |
| 3 - Execution and risk | IN PROGRESS | Simulated fee/spread/slip, entry caps, drawdown halt; no restart-safe order lifecycle, partial fills, daily-loss engine |
| 4 - Reward and evolution | NOT DONE | Do NOT award reproduction credit from unrealized PnL or synthetic path |
| 5 - Multi-agent architecture | NOT DONE | Existing components not yet integrated as audited, isolated agent contracts |
| 6 - Scientific backtesting | IN PROGRESS | Cash/hold/cost-stress comparative evaluator added; external Binance exploratory results expose 1h loss; walk-forward and uncertainty analysis missing |
| 7 - One-click dashboard | NOT DONE | Existing dashboard not connected to new paper CLI |
| 8 - Infra and state | PARTIAL EXISTING | Docker/Postgres/Redis exist; paper event journal and crash recovery unverified |
| 9 - CI and QA | BLOCKED | 17/17 isolated tests passed; repository-wide tests and Actions missing |
| 10 - Demo release gates | NOT READY | End-to-end UI/backend test, model proof and paper soak missing |

## Scope actually implemented and checked on this PR

1. `econith/quant/public_binance_data.py`: public unauthenticated
   `exchangeInfo` + `klines`, strict Spot status/rules, 1h closed-candle
   checks, gap/stale rejection, aligned asset universe, SHA-256 manifest and automatic checksum verification on replay.
2. `econith/quant/paper_v1.py`: offline multiasset cash-based replay,
   strictly previous-candle momentum signals, next-open simulated fills
   with both-side fees, spread and slippage, no shorting/leverage, exchange
   quantity rounding, per-symbol requested exposure caps, portfolio curve,
   drawdown halt; no real exchange calls.
3. Tests: `tests/test_paper_v1.py`,
   `tests/test_public_binance_data.py`,
   `tests/test_paper_binance_sample.py`: **20 no-fixture checks passed** by executing loaded source in an authorized Python REPL (including 3 evaluator checks), with full pytest/CI NOT verified. Earlier isolated suite reported 19 passed under an isolated
   Python/pytest test run on 2026-10-16.
4. Git blob hashes compared against tested local files, confirming
   the exact files proposed in the PR were the files tested. This
   does NOT mean all other files in the repository passed their tests.
5. Sample: public Binance BTC/USDT hourly market response in an
   eight-bar parser smoke test. No trade profitability claim is made.
6. Separate prior PR changes: oracle-gate repairs, isolated lagged
   baseline and live-router fail-closed guards. Their complete integration
   with the repository is not yet verified.

**Explicitly unsupported:** real exchange/demo trading session,
WebSocket incremental data, multi-venue adapters, tick-level liquidity,
partial fills, real-world maker/taker tiers, rebalancing on volatility
spikes, full risk management, persistence, model training, agent rewards
and reproduction, one-click dashboard.

## Current blockers / next engineering sequence

P0: GitHub Actions workflow fails with zero jobs on base main and PR;
investigate GitHub Actions account/org/policy/runtime state, see issue #26.
Full checkout/dependency installation and repository tests are not verified.
Desktop Commander is online but `F:/econith` is outside the device's
authorized directories (`F:/pham-store`, `F:/CHAM`), so no unsafe local
synchronization has been attempted. Permission must be granted locally
before the remote connector can safely access ECONITH.

P1: Build a `manifest + immutable snapshot -> causal feature extraction
-> frozen OOS model -> timestamped signals -> backtest` contract. Replace
placeholder 1h forward-label evaluation and execute multi-year trials
with realistic costs (#22-24).

P2: Implement durable replay/paper trade state, idempotent intent IDs,
separate trade/capital ledgers, reconciliation, simulated partial fills,
operator kill switch and tests for crash-after-fill and double-spend (#25).

P3: Only after valid closed-trade PnL exists, implement persistent
reward and evolution ledger with a strictly single-use 100-USD
eligibility event; additional quality gates and offline-only child agents.

P4: Connect the verified backend lifecycle to the dashboard Start/Pause/
Stop/Emergency Stop, then run acceptance tests and controlled paper soak.
Never enable live trading automatically.

**Release state:** NOT READY FOR DEMO TRADING.

## 2026-10-16 continuation — evidence and security

- Branch adds `scripts/evaluate_spot_replay.py` with cost sensitivity,
  equal-weight passive buy-and-hold and cash comparators, independently
  restarted historical windows; outputs fail-closed readiness status.
- `public_binance_data.py` now supports 1h and 1d captures, up to 1000 bars.
- Live mainnet execution in `quant/ccxt_bridge.py` hard-blocked during
  research. Disconnected `REALITY` returns REJECTED instead of synthetic fill.
  Additional behavior tests added, but full backend integration not run.
- Static syntax + **20 in-memory tests passed** for new standalone
  replay/capture/evaluation code (17 non-file tests plus 3 evaluation tests).
  The tests involving `tmp_path` are not included in the 20 checks.
- Initial Binance BTC/ETH/SOL test: 999 daily bars +15.24% on entire sample
  (but drawdown stop triggers), 999 hourly bars -9.19% with ~435 simulated
  fills, final 300 hours -5.44%, 3x costs -10.80%.
  These are independent numerical approximations, not full ENGINE results.
- All findings and replication steps documented in
  `docs/PRELIMINARY_CRYPTO_BACKTEST_20261016.md`.
- CI still fails with zero jobs, including pre-existing main runs.
- Status remains **NOT READY FOR HIGH-EFFICIENCY TRADING OR LIVE CAPITAL**.
