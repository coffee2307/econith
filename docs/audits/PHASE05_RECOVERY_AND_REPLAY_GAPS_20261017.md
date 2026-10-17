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
