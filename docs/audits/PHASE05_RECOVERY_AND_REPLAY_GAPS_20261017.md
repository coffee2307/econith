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
