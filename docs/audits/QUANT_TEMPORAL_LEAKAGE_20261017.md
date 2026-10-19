# Temporal leak hardening — ECONITH ML4T audit (2026-10-17)

**Scope:** Draft PR #28; independent research only. No trade execution, no main merge, no production wiring. Review methodology: the privately installed ECONITH Quant Skills (adapted from ML4T Agent Skills) for look-ahead bias, data leakage, purging/embargo, and backtest chronology.

## Confirmed code paths fixed

### Forward-label overlap at train/holdout

Previously, `training/quant/label_symbol.py` computed all `forward_return_*` values on the full per-symbol source timeline and then cut train/holdout by row ratio. Final train labels could use prices from the holdout, including long secondary horizons when the primary horizon was shorter.

Now the labeler:

1. Establishes each symbol's first holdout observation after per-symbol labeling; uses the **earliest** holdout timestamp across symbols as a conservative shared boundary.
2. Checks **every finite forward label** in a potential train row against its *actual selected source observation* (the first source timestamp >= decision timestamp + horizon), **not only the nominal end time**. Source timestamps must be positive and unique.
3. Excludes any train row with a label endpoint at or after the earliest holdout timestamp. Keeps holdout intact, returns `purged_train_rows` and `first_holdout_ts_ms` for audit. Fails closed when no causally eligible training rows remain.
4. Does **not** add `label_end` outcome columns to model features.

This avoids the cross-boundary price leak in the primary label and any finite secondary horizon, including sparse market data whose next actual observation arrives long after the nominal horizon. It is conservative for asynchronous assets, but does **not** certify cross-asset feature provenance or remove all train/test dependencies.

**Remaining:** Add an explicit per-event interval ledger and decision-window lineage, plus purging/embargo for overlapping repeated CV folds. A single train/holdout purge is not a full CPCV protocol. Historical training artifacts must be re-created before their metrics can be compared against this policy.

### Oracle access to evaluator outcome

Previously, `BacktestEngine.run_frame` passed the complete labeled DataFrame to the strategy `signal_fn`. A strategy could access `forward_return_1m` being scored and trivially predict perfect returns.

Now the evaluator constructs an isolated **deep-copied strategy feature view**, omitting columns named like `forward_*`, `future_*`, `target_*`, `label_*`, `reward_*`, `outcome_*`, `realized_*`, `next_*`, `&-*` and key exact/suffix labels. Scoring still uses the untouched authoritative forward returns. A deliberately oracle-seeking `signal_fn` receives a `KeyError`; ordinary lagged momentum and precomputed `oos_prediction_*` columns remain supported.

**Remaining:** A user-defined function can still compute future-looking transformations from later feature rows that are present in its DataFrame, or hide an oracle under an innocuous field name. This is an outcome-column barrier, **not** arbitrary-strategy causal proof. Production-quality evaluation requires an explicit, registered feature allowlist, prefix-invariance tests and rigorous decision/signal/fill timestamps. In particular, the vectorized backtest is not an event-driven venue execution simulator.

## Adversarial regression coverage

`tests/test_quant_temporal_safety.py` covers:

- Global earliest holdout boundary for asynchronous BTC/ETH sample counts, with all finite label horizons.
- Holdout price-poisoning invariance of retained train data.
- Sparse-source gap where nominal horizon precedes the cutoff but the **actual observed** target equals the first holdout tick.
- Invalid holdout ratios.
- Strategy cannot access any known future outcome columns; mutation isolation from evaluator.
- Deliberate oracle lookup is rejected.

The tests use offline synthetic data, no exchange access. GitHub Actions CI status must be checked on the **exact head commit** before recording a pass. Passing tests certify these invariants, not market source authenticity or expected profitability.

## Phase 1 downstream sequencing

The existing `OfflineMarketProjectionCursor` does **only** local, transactional verified projection storage and cursor advancement. It does not acknowledge EventBus processing, broker receipt or any external side effect. Do not wire raw market events to the default pipeline with an ephemeral ACK. A new opt-in durable outbox and independently idempotent receiver, including crash after publish / before ACK and restart tests, remain prerequisites.

## Risk boundaries

No cryptographic exchange attestation; no default market-input completeness guarantee; no synchronized order book; no live/capital permission. PR remains Draft and stacked on PR #21.
