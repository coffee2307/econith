# Phase 1 — operator-duration public Spot watch with honest coverage windows

**Status:** Opt-in, read-only SIMULATION operator research. This is a
*long-running supervised process with bounded, individually sealed Spot
source sessions*, NOT one uninterrupted WebSocket data stream. Every
bounded session uses a fresh public socket and REST snapshot, and the
interval between consecutive sessions is unknowable. No exchange account
credential, order route, default `main.py` cutover, production deploy or
trading authorization is part of this feature.

## What changed

`run_forward_research_supervisor(...,watch_duration_s=...,
persistent_receiver=True)` now supports an explicitly bounded operator
time target, from 5 seconds to 72 hours, with a separate 1–4096 session
ceiling, total requested-frame ceiling, max 2GB evidence disk ceiling,
per-session snapshot, bounded queue and up to 120s capture timeout.
It measures elapsed **monotonic** time; a trial finishing due to its
attempt ceiling records `WATCH_ATTEMPT_CEILING_REACHED`, **not**
`watch_target_reached=True`. The time target is checked after each
session, so the final bounded session can exceed the nominal target by
its per-session timeout. This is a duration goal, not a deadline timer
and not a real-time market-data SLA.

Before launching a session, the supervisor COMMITs its fresh
`rec_forward_` source ID as a `STARTED` row in the same root-local
SQLite `forward_supervisor.sqlite`. Every successful session reopens
the original immutable source/atomic-intent inventory, exact producer
outbox, independently authenticated receiver SQLite and three named
read-only derived observer ledgers, and only then records `PASSED`.
Failure writes a sanitized `FAILED` and durable outage row; later
valid fresh sources restore **present-time** market book only.

The new `forward_watch_plan` table records the originally requested
duration, attempt/disk limits, start time and whether the duration
target or session ceiling actually ended the operator trial. The new
`forward_watch_unobserved_boundary` table explicitly records **every
transition between finite sessions**, including two independently
successful sessions. Each has `coverage_verified=0` and a local
receipt-clock interval; even a zero-duration boundary does not
establish event completeness. If the host wall clock goes backward
between adjacent source sessions, the supervisor stops before adding
the next source or any fabricated boundary.

The existing `forward_watch_outage` also preserves
`coverage_verified=0` after restoration. A process hard exit leaves
an unclosed `STARTED` row and a root-local orphan owner lock requiring
manual review; it cannot silently resume in a different process or
overwrite earlier source bytes.

All runs are explicitly opt-in. `--watch-hours` is only accepted with
`--persistent-receiver`: each bounded source session then has one
independent local HMAC worker for multiple ACKs. One-shot mode remains
available for short finite regression controls.

## Example: operator-directed 24-hour research watch

Only run on a host authorized to access the ECONITH checkout with a
stable disk and Python installation. The tool opens **public Binance
Spot TLS/WSS and GET market-data endpoints** and checks public clock
plausibility *before each new session*. It never authenticates with an
exchange account or sends orders. This ChatGPT code delivery does not
execute the following operator trial on the user's Windows computer.

```powershell
Set-Location F:\econith
python -m ci.phase1_forward_endurance_trial `
  --allow-public-market-data `
  --persistent-receiver `
  --watch-hours 24 --attempts 2048 `
  --output-dir "$env:TEMP\econith-watch-fresh-20261018" `
  --max-frames 500 --timeout-s 120 --pause-s 0 `
  --max-storage-mb 1800 --max-book-staleness-ms 2500
```

The evidence directory MUST be fresh and absent. An attempt ceiling or
disk limit might be reached before 24h: the CLI must report an
unsuccessful time target rather than relabel a short observation as a
24h soak. Any failed source/outage also yields a non-zero exit, while
preserving its original and independent receipt evidence. No background
automation is created by these instructions.

Useful SQLite queries on saved `forward_supervisor.sqlite`:

```sql
SELECT planned_watch_duration_s, attempts_ceiling, storage_ceiling_bytes,
       admitted_at_ms, completed_at_ms, completion_code
  FROM forward_watch_plan;
SELECT iteration,session_id,status,failure_code,originals,
       receiver_acks,receiver_process_starts,max_receiver_ack_latency_ms,
       queue_high_watermark,recorded_storage_bytes
  FROM forward_watch_attempt ORDER BY iteration;
SELECT previous_iteration,next_iteration,previous_finished_ms,
       next_started_ms,coverage_verified
  FROM forward_watch_unobserved_boundary ORDER BY boundary_id;
SELECT opened_ms,restored_ms,last_failure_code,coverage_verified
  FROM forward_watch_outage ORDER BY outage_id;
```

## What these tests prove, and what remains open

Tests use offline fixture Spot frames but exercise the genuine source
SQLite, reconstructed as-of book, one real OS HMAC receiver process
per session, separate durable original/derived receipts and the
supervisor's persisted records. They verify that:
- A simulated time target stops after actual successful session proofs.
- An attempt ceiling below the requested watch duration is reported
  as incomplete, never PASS for the specified wall-time objective.
- Successful-to-successful session transitions are still permanently
  recorded as *unobserved market history*.
- Error/reconnect and restored book cannot change that gap marker.
- Every source attempt requires explicit preflight when configured.
- A rollback of the host wall clock prevents another session from
  being logged with invalid temporal ordering.

**Critically, none of the above certifies a true continuous 24/7
market-data source.** Next Phase 1 gates are a sustained single-socket
validated ingest/persistent receiver pipeline with bounded in-session
checkpoints, explicit reconnect invalidation/new full REST snapshot,
real measured 24–72h public Binance data/network/host failure evidence,
a fully reviewed default legacy-to-verified app migration, and full
multi-symbol/OHLCV/conditional derivative/macro provenance coverage.

The PR stays DRAFT. The full original Phase 1 remains OPEN.
