# Phase 1 — read-only forward supervisor, outage accounting and bounded trial

**Exit decision:** still OPEN. This is an opt-in **finite series of new
public Binance Spot sessions**, not a single continuous verified stream and
NOT a claim of gap-free exchange trading history. No producer, handler, route,
execution bridge, CCXT call or live account is added to default `main.py`.

## Design and evidence chain

The new `infrastructure/spot_forward_supervisor.py` calls the previously
reviewed `observe_forward_spot_research()` without replacing its transport
or source-event-time processing. Each trial attempt receives a fresh
`rec_forward_<random>` session, a fresh original SQLite capture and REST
snapshot, independent outbox and HMAC receiver SQLite, and three separately
durable read-only derived-observer ledgers. One root-local exclusive owner
lock protects the entire trial.

The supervisor writes `STARTED` to
`forward_supervisor.sqlite.forward_watch_attempt` **before** invoking the
clock preflight or socket. A source failure is marked `FAILED` and
a durable `forward_watch_outage` row opens; another attempt always starts a
**new** session/book, never reuses the suspected source. When a subsequent
independently verified attempt succeeds the outage is marked restored to a
present-time book, but `coverage_verified=0` remains immutable.

No outage reconstruction, no implicit backfill, no fabricated throughput and
no assumption that two finite successful rounds imply a continuous exchange
feed. Even sessions on consecutive attempts have an unobserved interval.
A hard process exit leaves `STARTED` and an orphan
`.forward_producer.lock`, intentionally requiring operator review rather
than auto-resume/rewriting evidence.

Each *successful* source is independently reopened: immutable original
records are matched against their atomic source-inventory receipts,
producer outbox envelopes and monotonic `DERIVED_ACKED`, and receiver-owned
receipts are matched **one-by-one** to the source envelope
`ingest_id/event_id/fingerprint/receipt_id`. Three read-only derived topic
counts are checked against the source-bound primary, audit and monitor
SQLite ledgers. A forged success object with absent files/ACKs cannot close
an outage.

Operational limits are enforced before network and again after each session:
1–16 attempts, 10–2000 WS frames per attempt, no more than 32,000 frames
requested in a run, 5–120s per attempt, 0–30s pause, a capped 2MB–2GB
on-disk budget, a bounded ingress FIFO, and book freshness threshold.
The status reports measured per-trial high-watermark and recorded disk bytes;
it does **not** claim measured CPU/RAM percentiles or 24-hour service
availability.

The new manual-only `ci/phase1_forward_endurance_trial.py` performs the
coarse public Binance time/RTT admission check **before every network
attempt**. When that clock check fails, the trial stops rather than
silently falling back to another market source.

## Manual isolated public-data trial

Run in an authorized ECONITH checkout with all reviewed Python dependencies.
Choose an output directory that **does not already exist**. It uses only
public Binance Spot TLS/WSS/REST GET endpoints. No API keys or exchange
orders are required or authorized.

```powershell
Set-Location F:\econith
python -m ci.phase1_forward_endurance_trial `
  --allow-public-market-data `
  --output-dir "$env:TEMP\econith-forward-watch-unique-01" `
  --attempts 3 --max-frames 160 `
  --timeout-s 45 --pause-s 1 `
  --max-storage-mb 100 --max-book-staleness-ms 2500
```

After completion, inspect the saved database using an SQLite viewer.
For example:

```sql
SELECT iteration, session_id, status, failure_code, originals,
       receiver_acks, queue_high_watermark, recorded_storage_bytes
FROM forward_watch_attempt ORDER BY iteration;

SELECT outage_id, opened_ms, restored_ms,
       last_failure_code, coverage_verified
FROM forward_watch_outage ORDER BY outage_id;
```

The CLI exits nonzero if any attempt failed, an outage remains open, or
the run did not produce any accepted session. Partial failures retain
their evidence. Do not delete or silently ignore the failed-attempt rows.
No autonomous restart/recovery after an OS-hard-exit is implemented;
the orphan lock intentionally prevents re-entry into a suspect root.

## Test scope and pending exit gates

Tests use real original-capture SQLite, actual local HMAC receiver child
processes and durable derived observers with **injected public frame
fixtures**. They verify success/failure/success with a new session and
an explicit outage, forged result denial, refusal on clock preflight,
cancellation leaving `STARTED`, resource-bound refusal and post-acceptance
tampering detection. CI is not evidence of live network availability.

Remaining requirements for **institutional Phase 1 100%**:

- Genuinely continuous source-to-receiver ingestion rather than repeated
  finite sessions, with a persistent receiver process and bounded
  sustained-throughput/backpressure evidence (not one new child per event).
- Real, operator-authorized 24–72h public Binance sample and host-level
  process/clock/network/resource fault injection. Quantify missing/unseen
  outage events as unknown, not zero.
- A documented, independently reviewed `main.py` default source cutover
  and inventory of legacy raw/derived publication and all effectful
  subscribers before any exposure to strategies/orders.
- Multi-symbol historical OHLCV precision/delist and applicable derivative
  funding/OI or macro release-vintage provenance tests.
- Independent operator sign-off and no production deployment or live order
  authorization solely on this evidence.

This supervisor **advances operational readiness**, but leaves the
full original Phase 1 exit OPEN and PR #28 unmerged.
