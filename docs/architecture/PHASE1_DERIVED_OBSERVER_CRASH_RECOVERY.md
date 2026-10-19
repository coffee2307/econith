# Phase 1 — full research consumer restart / hard-process-crash recovery acceptance

**Scope:** explicit opt-in SIMULATION-only, closed-original BTCUSDT Spot
research session. The source is read-only; all exchange/user/private trading
APIs, CCXT, live orders and legacy streamers remain disabled. This is a
research observer recovery contract, not globally exactly-once EventBus or
24/7 live ingestion.

## New crash/restart behavior

`start_verified_research_runtime(..., derived_receipt_db=ABSOLUTE_EXISTING_PATH)`
may reuse an **existing** per-derived-topic receipt SQLite ledger while
re-performing the entire closed raw-source → market provenance → independently
authenticated subprocess projection receiver verification in a **fresh**
`output_dir`. Recovery refuses a missing, relative, in-output or original
raw SQLite path as its writeable consumer ledger.

The derived observer writes the actual canonical payload + original-source
identity, ordinal, receipt timestamp and fingerprint atomically with its
consumer cursor. On replay of a committed event, an identical receipt is
returned with `replayed=True`; any drift in `ticker`, `volume_delta`, or
`OBI` source provenance or result is rejected. The verified runtime
reports `replayed_derived_research_receipts` separately from the number of
unique `durable_derived_research_receipts` so replay is not mistaken for
additional progress.

The research volume-delta series uses the **original source receipt clock**,
and every session's pure research state resets across an unobserved gap. The
derived output must remain byte-identical after restart even if wall clock
or processing speed changed.

### Crash-window experiments

`tests/test_spot_verified_research_runtime.py` adds three acceptance cases:

1. A successful replay, followed by a completely fresh independent
   verification and EventBus graph with the same original source and
   independently persisted consumer ledger. No duplicate receipts are added;
   all five derived receipts in the two-trade fixture report replayed.
2. A **real separate Python process** running the full original-source to
   authenticated-receiver research acceptance forcibly terminates using
   `os._exit(37)` immediately after two derived observer receipts have
   COMMITTED. The outer test proves both receipt and cursor survive the hard
   exit, and that a fresh verified research run completes the remaining three
   receipts while replaying the two previously committed receipts.
3. An attempted recovery that supplies the original evidence SQLite as its
   writeable derived receipt DB is refused before startup/side effects.

This is stronger than throwing a Python exception in the same event loop:
the hard-exit test is abrupt process death during source-to-consumer
processing, preserving only durable SQLite WAL transactions.

## Important limits

The code proves source-bound, deterministic, idempotent **local research
observer projections**. It does not guarantee atomic effects across the
separate independently authenticated source receiver DB and the derived
observer DB. It does not grant durability to arbitrary EventBus subscribers,
remote consumers, or orders. The real public collector still processes
**bounded sealed microbatches**, not continuous zero-gap low-latency frames.
A local passing crash/restart test and successful CI must not be used to
assert that all of Phase 1's original full-live ingress and prolonged-soak
acceptance gates have passed.

Only audited Draft PR #28 may be used; no main merge, production deploy,
external credentials, or real-money strategies.
