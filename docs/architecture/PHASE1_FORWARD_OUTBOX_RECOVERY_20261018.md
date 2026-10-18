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
