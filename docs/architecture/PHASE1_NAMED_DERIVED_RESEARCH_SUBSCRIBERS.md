# Phase 1 — independent named durable derived research subscribers

**Scope:** only opt-in sealed public Spot / historical SIMULATION research.
This strengthens the approved research observer acceptance path; it is NOT a
general-purpose transactional EventBus, risk engine, live feed, trading
authorization, default production cutover or proof of 24/7 availability.

## Why it exists

The previous verified research pipeline had one durable derived observer with
per-session/topic WAL/FULL receipts. Other subscribers returning from asyncio
handlers had only an in-memory completion barrier, which is insufficient to
prove independent durable consumer recovery. Source-to-subprocess producer
ACKs and that single derived observer were not evidence for any other consumer.

`infrastructure/market_named_research_subscribers.py` adds two **explicit
read-only consumer identities** (`research_audit`, `research_monitor`).
Each owns a separate SQLite database, an immutable persisted name/source policy,
and an independent per-session/per-derived-topic durable cursor/receipt.
Only exact canonical derived event projections are stored; there are no
external user callbacks or broker/order/portfolio side effects.

Both opt-in paths now register them:
- `infrastructure/spot_verified_research_runtime.py` (closed historical
  original source), with optional existing independent named ledgers for
  explicitly requested recovery.
- `infrastructure/spot_public_verified_microbatch.py` (multiple finite,
  sealed public or injected offline test sessions), always with fresh named
  ledgers and strict session separation.

Each named subscriber must validate `verified_session_id`,
`verified_source_record_id`, first local source availability, per-topic
ordinal, exact immutable derived projection, and source policy. Each accepted
message commits its projection and cursor in the same SQLite transaction.

The source is verified **before** the protected bus publishes events.
EventBus `wait_verified_idle` is required to report handler errors and
completion; then each named durable ledger is re-opened and independently
checked for exact expected receipt counts. EventBus completion **alone is not
a durable ACK**. An incomplete named consumer causes the entire research
acceptance to fail even if another consumer succeeded.

## Crash and replay contract

For historical replay, the recovery caller must supply **all** names with
existing absolute paths, not inside the new scratch proof directory. Paths
cannot alias each other, original source or primary research receipt database.
A ledger cannot be silently reopened under a different name or source policy.
Before ANY derived event can be published in a new run, the original source
and independent authenticated local subprocess source projection/receiver ACK
proof is repeated in a *fresh* research evidence directory.

After process hard exit following any named receipt COMMIT, an identical
canonical replay returns its original receipt without advancing cursor.
Before-COMMIT crashes leave no durable receipt and are processed on replay.
A mismatch in source/projection, skipped ordinal, changed policy, invalid
recovery path, wrong mode or incomplete expected count is **fail-closed**.
Cross-ledger atomic commit is intentionally absent; a partial fanout may
leave independently committed rows. Recovery is by verified source replay,
not by claiming a rolled-back global transaction.

## Evidence and limits

Dedicated regression tests:
`tests/test_market_named_research_subscribers.py` (real distinct Python
hard exit, independent cursors, forged source, conflicting replay, identity
collision, real guarded EventBus).
`tests/test_spot_verified_research_runtime.py` (historical opt-in ASGI,
separate-ledger recovery after independent fresh source verification).
`tests/test_spot_public_verified_microbatch.py` (two isolated finite sessions
and each receiver's durable ACK counts).

A 2-trade fixture emits 2 ticker + 2 volume delta + 1 orderbook imbalance;
each of the **two new** named consumers must own 5 rows and the existing
observer has 5. The new report distinguishes `durable_named_research_receipts`
from the prior `durable_derived_research_receipts`.

**Explicitly unverified:** arbitrary EventBus subscribers remain non-durable;
these two names are only restricted research projections, not side effects.
The **opt-in verified-only research graphs** additionally activate the
`VerifiedDerivedPublisher` capability fence. Direct `EventBus.publish`
to the three derived market topics is now rejected there, even with forged
source labels and IDs. Only `MarketDataPipeline` with its explicit capability
may publish these derived topics. This is a process-local capability, not
cryptographic proof against compromised code inside the same process. The
default/legacy general-purpose EventBus remains unchanged. No continuity is inferred between finite Spot capture sessions,
and a sealed final depth snapshot is not relabeled historical during the
preceding trade sequence. Continuous live ingestion, backpressure/soak,
multi-asset OHLCV/funding/vintage-source expansion, default runtime migration,
risk/order execution, capital and profitability remain separately gated.

PR #28 stays Draft; no main merge or real orders.
