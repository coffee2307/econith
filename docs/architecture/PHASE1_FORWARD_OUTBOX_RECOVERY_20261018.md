# Phase 1 — Forward Spot producer outbox & staged-only crash reconciliation

**Status:** reviewed SIMULATION-only research facility, NOT a continuous certified
feed, full-session recovery, exactly-once external effect, real trading
authorization, or default `main.py` cutover.

## Original failure window

The first in-session forward shadow persisted raw original rows, called the
authenticated local receiver, and published into a guarded read-only EventBus.
A process death after the receiver committed but before EventBus observer
commit could leave multiple independent ledgers without a durable producer
**intent** or a knowable stage. A subsequent independent receiver ACK never
automatically means the derived observer also committed.

## Changes

`infrastructure/spot_forward_outbox.py` provides a source-session-scoped
SQLite WAL/FULL durable producer outbox. Prior to opening a receiver process,
the source-bound *immutable* canonical `ProjectionEnvelope` is COMMITted.
A state machine advances monotonically through:

- `STAGED`: full typed receiver intent durably stored; no receiver ACK assumed.
- `RECEIVER_ACKED`: exact receiver-owned HMAC-authenticated receipt was
  verified and independently COMMITted; raw EventBus outcome still unknown.
- `DERIVED_ACKED`: reviewed guarded derived subscriber graph completion
  observed; all named observer handlers write their own durable receipt
  before returning. Exact per-topic cursor totals are rechecked at research
  session acceptance.

`infrastructure/spot_forward_verified_research.py` uses this outbox at
each per-committed-original admission. An event is never sent to the
receiver before intent STAGE. Identical intent/ACK replay is idempotent.
Different payload/source/time for the same `ingest_id` fails closed.

`infrastructure/spot_forward_recovery.py` reconciles only the *already
staged* immutable envelopes, in persisted ingest order. It verifies each
source original from the capture journal by ID/fingerprint/timing, resends
the same envelope over the reviewed HMAC subprocess pipe, and fully replays
the research pipeline into the preexisting primary/audit/monitor SQLite
receipt ledgers. Previously persisted exact receipts are accepted
idempotently; conflicting derived values or missing ordinal boundaries
reject recovery. Recovery reports explicitly set
`source_coverage_certified=false` and `session_certified=false`,
regardless of how many staged messages pass.

**Critical non-atomic window:** original raw rows are COMMITted before the
optional `on_persisted` callback stages its forward outbox intent. A hard
exit exactly in this gap leaves raw originals without a matching outbox
record; **this code does NOT infer or repair them automatically**. Operator
source-rescan and eligibility reconstruction are still pending. Even a
perfect staged recovery cannot claim complete source coverage. The outbox,
receiver and independent derived ledgers do not share one ACID transaction.

## Manual recovery on an isolated, stopped source

From an authorized ECONITH checkout after stopping the research process:

```powershell
python -m ci.phase1_recover_forward_outbox `
  --research-recover `
  --output-dir "$env:TEMP\econith-forward-spot-new-01" `
  --session-id "rec_forward_<exact-original-id>" `
  --max-envelopes 2000
```

Never run recovery concurrently with the original WebSocket publisher.
A source with an unclosed crashed session must be quarantined; the
recovered output is evidence about staged research rows ONLY, not a
successful/complete capture session. Recovery never opens public network,
submits orders, rebuilds a new original source, substitutes reconstructed
market frames or changes `main.py`. Existing SQLite files for source,
outbox, receiver, primary derived and both named durable observers must
already exist. If one is missing, fail closed rather than create a falsely
complete receipt database.

## Verified and deferred

Automated tests exercise immutable outbox identity, collision refusal,
real Python `os._exit` after the independent receiver COMMIT, and
the receiver/derived ACK-fault windows against the full existing Spot
fixture transport and actual HMAC local subprocess. CI success is bounded
research evidence, not a 24-hour live market soak.

Explicit follow-up work:
- source/raw-to-outbox atomicity or independently verified original-rescan
  recovery of raw rows that failed to STAGE;
- durable process ownership/fencing of the outbox, so no second process can
  recover concurrently with an active producer (manual stop mandatory now);
- robust bounded high-throughput receiver transport and crash/reconnect E2E;
- long-running continuous source capture with event-as-of correctness,
  subscriber delay/backpressure, source outage windows and measured
  performance under actual Binance public traffic;
- multi-asset/historical data coverage and audit of default legacy
  application paths before verified-only cutover.

**Overall institutional Phase 1 remains OPEN.**


## Follow-up correction: atomic capture-source inventory and owner fence (2026-10-18)

At HEAD `05c4dc87a29ae32c1d87091c4bd7203dec38520d`,
`OfflineSpotCaptureJournal(forward_capture_audit=True)` now inserts an
`offline_forward_capture_intents` row **inside the SAME SQLite
transaction as each immutable original record**. Source-write failure,
intent-write failure or database-full errors roll back both. The
`verify_forward_capture_inventory` method compares every raw record
with its source-intent ID, kind, receipt time and SHA-256, and
independently re-reads the original bytes. The forward runtime requires
the entire captured source inventory to match its transport/shadow
record counts before successful certification.

`exclusive_forward_owner` now guards the **entire opt-in forward
research run** and **manual recovery** in the same evidence root via an
atomic OS create-exclusive lock; a concurrent recovery cannot attach
to an active source. A hard process exit leaves an orphan lock that
requires deliberate operator investigation before another attempt.
This is a local host ownership fence, not an HA distributed leader lease.

**Important distinction:** the atomic capture-intent record is an
inventory of *every received raw frame*, not an already-approved,
book-synchronized derived `ProjectionEnvelope`. A crash can still
occur between atomic original+inventory COMMIT and the later eligibility
calculation / producer outbox STAGE. All such raw rows are **discoverable
and auditable** from the immutable source inventory, but the currently
approved staged-only recovery DOES NOT automatically reconstruct and
certify previously unstaged forward effects. It continues to report
`source_coverage_certified=false`, `session_certified=false`.

New tests assert intent INSERT errors roll back the original source
record, tampered/missing inventory blocks certification, competing
producers/recovery fail closed, orphan locks cannot be silently stolen
and an operator-reviewed recovery remains idempotent.

**Final same-commit CI evidence:** [CI #39099273369](https://github.com/coffee2307/econith/actions/runs/39099273369)
(699 passed, 1 skipped), [Docker images #39099273358](https://github.com/coffee2307/econith/actions/runs/39099273358)
and [container smoke #39099273351](https://github.com/coffee2307/econith/actions/runs/39099273351) all succeeded.

This is a meaningful correction to the previous raw→outbox gap,
**not** certification of 24/7 Binance data availability, lossless
feed completeness, multi-asset history or the default runtime migration.
