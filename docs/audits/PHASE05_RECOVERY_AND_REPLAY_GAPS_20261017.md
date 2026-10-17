# ECONITH — Phase 0.5 durability, restore and order-replay readiness

**Date:** 2026-10-17  
**Status:** Draft safety review, not an operational sign-off.  
**Scope:** disposable GitHub Actions Docker services; no user data, exchange accounts,
mainnet keys or live orders are present.

## What the current gates prove

- Backend starts using synthetic market-data provenance; uncredentialed API
  mutations are rejected and their decisions recorded in durable audit logs.
- The `StateRecorder` and legacy `TradeStateStore` each have an independent
  SQLite journal on a named volume. On a **graceful Docker restart**, committed
  synthetic probe rows and the denial audit log are retained.
- Production and staging PostgreSQL probe failures prevent startup rather
  than silently switching to an uncoordinated local SQLite ledger.
- `.github/workflows/container-smoke.yml` adds two stricter drills:
  (1) `pg_dump -Fc` of the entire ephemeral Postgres database and
  `pg_restore` into a **separate** disposable database; verify source and
  restored rows match, and (2) SIGKILL the disposable backend and verify
  committed SQLite rows and audit trail remain intact after recovery.
  **Do not mark these verified until the corresponding Actions run succeeds.**

### What this cannot prove

A successful full *logical* `pg_dump` and separate-`pg_restore` exercise
proves logical content/DDL can be restored in the test environment, **not**
point-in-time recovery (PITR), physical replication, WAL archiving, hot
snapshot correctness, recovery time objective, or recovery point objective.

The forced-kill canary checks **already committed** SQLite writes; it does
not prove zero loss for uncommitted transactions, exactly-once EventBus
delivery, atomic PostgreSQL+SQLite writes, or survival of destruction of a
host/container volume.

## Critical unresolved blocker: intent replay and broker reconciliation

Current code is not certified for exactly-once broker execution:

1. `bridges/quant_bridge.py:QuantExecutionBridge._build_payload` always
   chooses `_client_id(symbol)`. `_client_id` derives a new SHA-1 hash
   from the wall-clock timestamp. **Replaying the same order intent
   generates a different client ID.** This is not a replay-safe identity.
2. `econith_quant/recovery/state.py:TradeStateStore` stores order lifecycle
   in a SQLite table with auto-increment `id`, but **no unique exchange
   `client_order_id`**, broker ID, or durable atomic intent admission.
   `record` catches and only debug-logs write failures.
3. `econith_quant/bridge/exchange_bridge.py` is an opt-in **mock**
   TWAP lifecycle and must not be treated as proof of real broker
   reconciliation. Its REALITY path is deliberately disabled.
4. `quant/ccxt_bridge.py` reports uncertain exchange outcomes as
   `UNKNOWN` rather than fabricated FILLED, which is important, but
   it is not an end-to-end recovery protocol for network timeout or
   broker acknowledgment after a process crash.

**Required design prior to capital authorization (separate reviewed PR):**

- Assign a stable, event-origin `intent_id` once when a signal is
  accepted, persist it before any broker submission, and include it in
  every retry/replay; don't generate a new ID on each reception.
- Use a transactional durable outbox/inbox or admission ledger with
  unique `intent_id` plus a stable exchange-client-order ID and
  explicit states such as `PENDING`, `SUBMITTING`, `ACKNOWLEDGED`,
  `UNKNOWN`, `RECONCILED` and `REJECTED`. Define state transitions
  and atomicity for the authoritative single writer.
- On `UNKNOWN` or restart, **query the broker using the original client
  ID** and independently reconcile fills, open orders and account positions
  before any submission/retry. For venues without a safe idempotency
  contract, block automatic retries and require a human-signed resolution.
- Reconcile the authoritative ledger to broker history with explicit
  timeouts; compare position exposure, fees, partial fills and order IDs.
  No synthetic World or mock TWAP record may satisfy broker evidence.
- Prove repeated EventBus delivery, two simultaneous consumers, mid-send
  timeout, lost ACK, partial execution, network partition and crash
  after broker submission **cannot double-place a live order**.
- Add immutable fault-injection test fixtures and a separately reviewed
  promotion gate; retain the existing live-execution deny policy until all
  are satisfied.

**Conclusion:** Phase 0.5 is a storage-recovery improvement, not a safe
autonomous-trading or profit-readiness certification. Do not infer exactly-once
execution from healthy Docker or SQLite `PRAGMA quick_check`.

## Operational constraints

- No commands on production volumes, no `docker compose down -v` outside
  disposable CI, no shared trading API keys, no destructive host cleanup.
- A real deployment needs a **backup-first migration** of the two legacy
  SQLite journals, audit files and PostgreSQL databases. See
  `docs/operations/DOCKER_STATE_VOLUMES.md`.
- The orphan `econith_social` Git gitlink and GPL/MIT boundary within
  `econith_quant/` are not yet resolved. PR #28 is draft and based on
  draft PR #21; `main` is untouched.


## WAL-aware SQLite snapshot tool (reviewed code, explicit/manual execution)

`ci/sqlite_consistent_backup.py` provides a manually invoked, **source
read-only**, online SQLite snapshot using `sqlite3.Connection.backup`.
Unlike a naive file-system `cp`, it includes already committed rows
which may still live only in the `-wal` sidecar.

```bash
# Works on a separately authorized dev/staging copy. Specify real file
# locations yourself; do not automate against live trading infrastructure.
mkdir -p backups
python -m ci.sqlite_consistent_backup \
  --source datasets/econith.sqlite \
  --destination backups/econith-state-20261017.sqlite
python -m ci.sqlite_consistent_backup \
  --source datasets/trades.sqlite \
  --destination backups/trades-state-20261017.sqlite
```

The tool creates only the destination snapshot, verifies SQLite
`PRAGMA quick_check`, publishes the new file with an atomic
no-overwrite link, and cleans temporary files on failure. It never
silently overwrites an existing backup, creates a missing source,
executes production tasks or modifies the original journal.
It does not migrate production data and is **not a multi-journal
transaction**: snapshots of `econith.sqlite` and `trades.sqlite`
are taken separately, so their time boundaries may differ. A
coordinated maintenance checkpoint and broker/ledger reconciliation
are still required for a full production restore.

Unit tests include an *uncheckpointed committed WAL row* case,
existing-destination refusal, invalid source refusal, missing path
and explicit CLI entrypoint. See `tests/test_sqlite_consistent_backup.py`.


## Offline admission prototype — no broker execution

The review branch now includes `quant/offline_admission.py` with
`OfflineAdmissionLedger` and `quant/offline_replay_probe.py` with
`OfflineReplayProbe`. These are deliberately **not wired to `main.py` or
the real `QuantExecutionBridge` execution flow**. They are research-only
safety primitives for independent review.

The ledger uses a durable SQLite WAL file with synchronous=FULL, a unique
`intent_id`, a unique stable hashed `client_order_id`, immutable canonical
payload fingerprint, and `BEGIN IMMEDIATE` admission transactions. If the
same `intent_id` is delivered twice with matching semantics, the second
delivery yields `created=False`; if its content differs it is rejected.

The discrete offline states are `ADMITTED`, `SUBMITTING`, `UNKNOWN`,
`ACKNOWLEDGED`, `RECONCILED`, `REJECTED`. A simulated submission claim
may succeed only once. On a crash, interrupted `SUBMITTING` rows are
moved to `UNKNOWN`, not to an automatically retryable state. A supplied
read-only fake-broker lookup must report the same client ID and an actual
broker order ID before an acknowledged/terminal state can be recorded.
`NOT_FOUND` is ambiguous, **not** permission to submit a replacement.
A timeout, contradictory order ID, zero-volume claimed fill or regressing
filled quantity remains `UNKNOWN`.

`OfflineReplayProbe` consumes copied `EventBus` `order.intent` events
only in SIMULATION, requires an explicit origin `intent_id` and returns
NEW/DUPLICATE/QUARANTINED diagnostics. It has no broker adapter,
no send/resubmit method and never emits a fake fill.

Corresponding regression tests:
`tests/test_offline_order_admission.py`,
`tests/test_offline_replay_probe.py`, and a direct REALITY hard-deny
test in `tests/test_execution_truthfulness.py`.

**Additional execution hard stop:** `bridges/quant_bridge.py` now explicitly
rejects all REALITY order intents with
`durable_order_admission_not_certified` if they somehow pass the existing
predictive edge gate. Even future edge_source=model cannot authorize
broker execution until the durable ledger is integrated and independently
reviewed. SIMULATION routing is unchanged.

**Remaining gates before any broker integration:** assign immutable
origin IDs in the actual producer with a durable source event/outbox;
ensure idempotent submission across multi-process services; perform
read-only broker reconciliation using verified account/venue scope and
partial-fill exposure; guarantee a single authorized executor; protect
against race with actual order placement and broker eventual consistency;
test orphan order reconciliation and position snapshots; add recovery
operator procedure and external review of license/compliance. This offline
model does not claim exactly-once exchange execution.

No network/exchange calls, no broker credentials, no real orders or main
merges were performed for this prototype.


## Phase 0.5: optional predictor-origin outbox and per-leg intent provenance

The `Predictor` is the actual `ai.signal` producer, and `AIBridge`
translates those advisory events into `order.intent` events. Previously no
stable source identity traveled along that boundary.

This PR now contains `quant/offline_signal_outbox.py`:
- Explicit `OfflineSignalOutbox(path, namespace=...)` maintains a persistent
  SQLite WAL-backed event journal and assigns a unique `sig:<namespace>:<sequence>`
  origin identity **at the time the decision is first persisted**.
- Every `STAGED` decision is committed before EventBus publication; after
  enqueue it remains `QUEUED`, which is *not* proof of consumer processing.
  Unacknowledged rows remain available for explicit/manual replay with the
  exact original serialized decision payload and `signal_id`. Only a
  separate downstream-confirmed `acknowledge` retires a row.
- `Predictor(..., offline_signal_outbox=...)` is strictly an **opt-in**
  SIMULATION research path. **main.py does not enable it.** Omitting the
  argument retains the legacy producer behavior, and attempting to use an
  outbox in REALITY fails closed.
- When an `ai.signal` has a valid origin `signal_id`, the existing
  Sentinel-gated `AIBridge` forwards that ID and derives a stable, distinct
  `intent_id` per routed leg from the origin ID, leg index and symbol.
  It rejects malformed supplied signal IDs rather than forging replacements.
  Without the optional origin ID, legacy simulation signals remain compatible;
  they still lack a certified durable identity and cannot authorize real orders.
- Tests in `tests/test_offline_signal_outbox.py` exercise crash-before-
  enqueue recovery, explicit replay, forged-ID refusal, SIMULATION-only
  enforcement, default Predictor compatibility and repeated-leg identity.

**Boundaries and remaining risk:**
- Staging a newly computed signal produces a **new** outbox sequence. If
  upstream market inputs are reprocessed and the Predictor recomputes a signal
  *before it was staged*, this alone cannot deduplicate the recomputation.
  Proper durability requires an independently stable market/input event key
  at ingress, and an atomic uniqueness policy linking it to the decision.
- EventBus only queues an event; `QUEUED` does not imply delivered or
  persisted by the downstream consumer. The opt-in path currently has no
  production acknowledgement handshake. Neither `main.py` nor the capital
  routing path consumes this outbox, and automatic replay is deliberately
  disabled.
- A replayed signal may encounter a different in-memory exposure or route
  profile. Therefore identical `intent_id` with a changed business payload
  must be quarantined by the *future wired* intent admission ledger.
  The existing disconnected `OfflineAdmissionLedger` already rejects
  such conflicts, but no whole-system atomicity guarantee is claimed.
- Stable `intent_id` does not currently replace
  `QuantExecutionBridge._client_id`'s timestamp-derived identifier.
  **REALITY remains unconditionally blocked** pending an independently
  reviewed broker-reconciled admission and single-writer implementation.

These are research and lineage improvements, **not visual Dashboard changes**
or proof of exactly-once exchange execution.


## Phase 0.5: downstream-confirmed durable receipt ACK (SIMULATION ONLY)

`quant/offline_ack_bridge.py` implements a **research-only** acknowledgement
consumer for `order.intent` and `offline.signal.ack`. It is deliberately
**not registered in `main.py`**, cannot connect to an exchange and has no
CCXT order-sending API. Deployment requires explicit creation and registration
in a separately controlled simulation experiment.

Flow:

1. The opt-in `OfflineSignalOutbox` assigns and commits a source `signal_id`
   and immutable signal payload **before** queuing `ai.signal`.
2. AIBridge attaches the source `signal_id`, SHA-256 source fingerprint,
   deterministic per-leg `intent_id`, 0-based `leg_index` and exact
   `leg_count` to each intent whose source was outbox-identified.
   Legacy intents without an origin ID remain compatible but cannot generate
   a research receipt or authorize REALITY execution.
3. The separate `OfflineSignalAckBridge` requires SIMULATION mode, confirms
   the exact signal exists in the outbox and its payload hash matches, checks
   every leg's deterministic ID, integer index and count, side, finite positive
   quantity and reduce_only semantics. It writes the admission and leg receipt
   in **one SQLite transaction** in the `OfflineAdmissionLedger` database.
4. Only when all declared leg indices are persisted does the durable
   `offline_signal_receipts` row become `COMMITTED`. At that point
   the bridge emits the in-process `offline.signal.ack` signal.
5. A separate ACK handler **does not trust the EventBus message**.
   It rechecks the immutable source payload and the complete durable receipt,
   then retires the matching queued signal in the original outbox.
   Concurrent duplicate acknowledgements are idempotent.
6. If the consumer crashes after the receipt commit and before ACK,
   `replay_verified_acks()` explicitly reissues **only verified receipt
   acknowledgements**, never a new `order.intent` and never a broker order.
   If the original EventBus queue is lost before the consumer runs,
   manual retransmission of a `QUEUED` outbox entry reuses its original ID.

Tests in `tests/test_offline_signal_ack.py` cover the real EventBus and
AIBridge path, partial two-leg receipt, missing or forged acknowledgements,
duplicate/changed legs, interrupted multi-leg writes, crash after receipt
before ACK, queue loss before dispatch, parallel ACKs and REALITY isolation.
`OfflineAdmissionLedger` rows remain `ADMITTED`: receiving local ACK
**never claims a fill, exchange acknowledgement or investment performance**.

**Remaining limitations / release blockers:** SQLite outbox and inbox are
two independently committed journals, so a gap between the commits remains.
The protocol is at-least-once and attempts safe replay, not exactly-once
delivery. `AIBridge`'s exposure, prices, route profile and Sentinel state
are still mutable in memory; reconstructing an incomplete multi-leg plan
after a crash may produce different sizes. The ledger then refuses to
reconcile the changed business intent, leaving the signal pending. A
production-grade design needs a frozen durable complete order plan,
upstream unique market-event IDs, a single executor, full broker position/
order reconciliation and fault injection in a separately isolated staging
network. No automatically active recovery/ACK or trading permission has
been added to `main.py`.

**Capital execution remains hard-blocked in REALITY.** This feature
supports correctness testing only and must not be used as a production
order-admission certification.


## Phase 0.5: frozen per-signal routing plan (opt-in SIMULATION research)

The optional `AIBridge(..., offline_signal_outbox=box,
simulation_mode_provider=...)` path snapshots the **entire**
`quant.route.plan` metadata and all `order.intent` payloads in
`offline_frozen_route_plans` inside the **same source SQLite outbox
database**. Its `BEGIN IMMEDIATE` transaction verifies the original
staged source, stores a canonical JSON snapshot plus hashes, and commits
before publishing *any* route-plan or order-intent EventBus event.

For a known `signal_id`, subsequent deliveries and process restarts
read the exact stored plan instead of re-running mutable AIBridge size,
mark-price, derisk, portfolio exposure or router-profile calculations.
A competing first writer with different business data is rejected; it
cannot replace the frozen snapshot. The optional offline ACK consumer
rejects any leg that differs from a frozen leg, and re-verifies all
stored leg fingerprints before considering a durable receipt complete.

The original AIBridge constructor remains fully backward compatible:
without an explicitly provided outbox **and** SIMULATION mode provider,
the new snapshot behavior is not enabled. Sentinel `FROZEN`, incompatible
`REDUCE_ONLY` or any active non-SIMULATION mode veto delivery and
re-delivery, even when the snapshot already exists. The local isolated
ledger still cannot place broker orders; REALITY execution remains
independently blocked by `QuantExecutionBridge`. No default runtime
wiring, automatic recovery, or production migration is included.

The new regression tests in `tests/test_offline_frozen_route_plan.py`
cover persisted-plan identity, restarts with changed exposure/marks,
second-writer conflicts, forged first-delivery leg rejection, Sentinel
transition and REALITY isolation, post-ACK replay refusal, and mandatory
explicit mode provider. Existing real EventBus and standalone ACK tests
remain separate. Results must be confirmed by GitHub CI on the new head.

**Explicit remaining limits:** immutable routing begins at the first
successful plan freeze, *not* at upstream market event ingestion.
Crashes before a first plan freeze can still cause a new computation;
new Predictor signals require immutable ingress keys for source-level
deduplication. Snapshot and local inbox commits remain on two SQLite
databases without distributed atomicity. In-memory exposure for *new*
decisions is not an authoritative, broker-reconciled position after
restart. `QUEUED` can still race ahead of a verified receipt ACK; manual
receipt replay is necessary for that gap. No broker fills, external
position reconciliation, or exactly-once exchange execution are proven.


## Phase 0.5 — source market-event identity and atomic input-to-decision binding (SIMULATION only)

`OfflineSignalOutbox.stage_for_event(source_event_id, source_payload,
decision, mode=QuantMode.SIMULATION)` is an explicitly opted-in
research primitive. The input event ID must be provided by the upstream
caller. **It is never generated from the wall clock, hash of a mutable
feature dictionary or price alone.** The caller also provides the exact
market-event payload used as the binding provenance. Neither this
implementation nor the current ticker collector authenticates that
provenance against an exchange.

A new `offline_input_decisions` SQLite table stores the caller-origin
event ID, canonical finite JSON market payload, its SHA-256 digest,
namespace, and referenced output signal sequence. The unique
`(namespace,source_event_id)` constraint and a serialized
`BEGIN IMMEDIATE` transaction commit the complete input association
**atomically** with the decision stored in `offline_signal_outbox`.
Concurrent retries cannot allocate a second signal for the same source ID.

For an already persisted matching source event, retries recover the
original signal and *original decision*, even if a newly computed decision
or model version would differ. A reused ID with different source JSON is
rejected with `SourceEventConflict`, not silently attached to the
previous signal. Distinct source IDs remain distinct even with identical
price/JSON payloads; hashing only the market price would incorrectly
collapse legitimate repeated events.

The opt-in `Predictor.publish_for_market_event(source_event_id,
source_payload,decision_factory)` first checks the durable source
mapping: the decision factory is not rerun if a matching record already
exists, preventing unnecessary nondeterministic re-inference on replay.
It queues only non-ACKED envelopes and follows the existing verified
consumer/ACK route. REALITY or missing outbox fail closed. The original
cadence-based `Predictor.run()` is **unchanged**, so new functionality
is **not** available in the default tick processing pipeline and does
not alter `main.py`.

`tests/test_offline_market_event_identity.py` covers concurrent
first writes, restart/re-inference reuse, changed source rejection,
distinct event identities, SQLite injected-write rollback, malformed
or nonfinite provenance, REALITY isolation, stopped factory after
ACK, and full opt-in EventBus/AIBridge/ledger receipt integration.

**Remaining work before claiming ingestion identity correctness:**
(1) exchange-specific collector must emit authoritative immutable
stream/sequence IDs together with point-in-time event payloads;
(2) verify out-of-order, gaps, REST/WS overlap, exchange-session resets
and duplicate sequence scopes before accepting those keys;
(3) explicitly define a stable decision-window key when multiple
indicators/events contribute to one cadence-based inference;
(4) persist input snapshots **before** inference if failures before the
first commit must be recovered without recomputation;
(5) document tamper evidence and authenticated source provenance
outside an untrusted local SQLite hash;
(6) retain independent risk/venue authorization and reconciliation.

This design improves **local simulation source deduplication**, not
production market-data correctness, broker idempotency, capital
authorization, or profitability. No live orders, demo-account keys,
automatic replay, main merge or production data migrations are
performed by this change.
