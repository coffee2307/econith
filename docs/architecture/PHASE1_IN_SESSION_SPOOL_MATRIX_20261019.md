# ECONITH Phase 1 — in-session durable spool projector pilot & 3×1,000 findings

**Recorded:** 2026-10-19. **Scope:** draft PR #28, public read-only BTCUSDT or injected deterministic offline fixtures. This record does not certify institutional Phase 1, continuous gap-free market data, cutover to default runtime, financial performance or live orders.

## What changed

- `infrastructure/spot_projection_durable_cursor.py`: new separately owned WAL/FULL SQLite cursor, strict +1 original ordinal; compares committed original bytes SHA256 and same-transaction `offline_forward_capture_intents` before advancing, checks identity, supports reopening with verification, fails closed on altered or skipped originals. Cursor advances **after** HMAC receiver COMMIT, durable named derived observations, producer outbox-derived ACK and chained checkpoint; no false cross-DB atomicity claims.
- `observe_forward_spot_research(..., spooled_in_session=True)`: **opt-in** indexed-original async worker runs while a separate ingress validator commits and verifies incoming originals; no in-memory raw payload queue added. Producer signal only announces a durable original ID. Worker re-reads committed bytes and validates its independent as-of book, then executes the unchanged authenticated research graph and commits cursor. It maintains a bounded original backlog, raw byte ceiling, publication-age gate before HMAC stage and **again immediately before protected EventBus publication**, and final whole-session cursor/source/outbox/receiver/observer/checkpoint comparison. Failed/unknown source remains quarantined.
- `ci.phase1_offline_burst_probe --live-spool` permits local synthetic comparisons; `ci.phase1_public_live_spool_trial` is a separate manual one-shot public test requiring BOTH public data consent flags, a brand-new absolute path, public HTTPS clock preflight, one fresh WSS/REST source, mandatory persistent signed independent child receiver, fixed 128 ingress, max 1,000 WS frames, 120s capture, 5,000ms max projector age and at most 512 pending original records. `ci.phase1_public_live_spool_matrix` runs 3 isolated attempts and writes a non-destructive source-independent `matrix_report.json`; **one failure is never masked**.
- Default application, legacy/production market adapter and the previously reviewed operator `Load` are **unchanged**. The in-session spool mode is NOT automatically started. Closed or incomplete source histories are never promoted to gap-free.

## Reproducible local evidence

| Probe | Source | Result | Acceptance-relevant observations |
| --- | --- | --- | --- |
| Offline in-session worker, 30 synthetic WS frames, 60ms spacing | fixture | PASS | 31 original incl REST snapshot, 29 independent receiver ACKs, 31 original cursor, input FIFO peak 2/128, pending peak 2, max source age 199ms, public source still OPEN when 3+ ACKs observed |
| Offline in-session worker, 240 synthetic WS frames, 60ms spacing | fixture | PASS | 241 original, 239 receiver ACK, cursor 241, FIFO peak 2, pending 9, age peak 455ms, source-open publication |
| Offline in-session worker, 240 synthetic WS frames, 3ms spacing | fixture | **QUARANTINED** | Source/projector cannot meet bounded freshness; worker rejects rather than publishing stale raw data |
| One independently clock-checked public Binance BTCUSDT pilot | REAL public TLS WSS/REST, 160 frames | PASS finite in-session research | 161 originals, 141 ACK/checkpoints, cursor 161, queue peak 16/128, pending 29, max source age 1,624ms, zero orders, exit 0, 19.65s |
| One fresh public BTCUSDT pilot | REAL public TLS WSS/REST, 1,000 frames | PASS finite in-session research | 1,001 originals, 990 ACK/checkpoints, cursor 1,001, queue peak 26/128, pending peak 68, max age 3,924ms, zero orders, exit 0, 85.94s |
| **Three fresh public BTCUSDT segments of 1,000 frames** | REAL public TLS WSS/REST, exact finite matrix | **FAIL: 1/3** | Attempt 1 rejects `FORWARD_PROJECTOR_SOURCE_LAG`; attempt 2 rejects `FORWARD_PROJECTOR_SOURCE_LAG`; attempt 3 PASS: 1,001 originals, 992 ACK/checkpoints, cursor 1,001, queue peak 43/128, pending peak 76, max age 4,203ms. Matrix elapsed 150.657s, exit 1; orders 0 |

All output resides at operator-host `F:\ECONITH-Phase1-Evidence\`, isolated per test, never moved into the Git repo. Key real per-run roots:
- `20261019_public_live_spool_160_v1`
- `20261019_public_live_spool_1000_v1`
- `20261019_public_live_spool_matrix_3x1000_v1`

Read-only postmortem SQLite counts (failed-source rows are forensic ONLY):

| Matrix attempt | Durable source originals | Independent original projector cursor | Receiver ACK rows | Chained checkpoint rows | Still unprocessed original suffix |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 — FAIL | **600** | 500 | 490 | 490 | 100 |
| 2 — FAIL | **528** | 352 | 341 | 341 | 176 |
| 3 — PASS | **1,001** | 1,001 | 992 | 992 | 0 |

The failed attempts establish a new, more specific throughput limit: **source commit is no longer blocked on the complete downstream graph**, but in source bursts the projection worker falls behind the source's receipt clock and properly quarantines itself at the 5s delivery-age budget. The legacy WebSocket FIFO 128/128 problem is no longer the *observed failure in this opt-in mode*. That does not establish that old in-session `Load` or higher-volume sources are fixed.

## Cross-database failure proof

Tests explicitly inject a crash/error after independently durable receiver/derived/checkpoint commit but **before original spool cursor advance**. The trial aborts; original source is closed/quarantined, no `forward_complete.json` is created and cursor is allowed to lag a verified checkpoint. This boundary is NOT made fictitiously atomic. Existing operator-invoked staged recovery may inspect and reconcile a closed prefix but may NOT automatically certify an incomplete source or resume a WSS gap.

## Open engineering work — no hidden relaxation of gates

1. Profile projector throughput under known synthetic burst schedules and, if necessary, a fresh bounded public measurement: record end-to-end original→source-bound receiver→derived→checkpoint/cursor latency distributions and pending-durable-original history; limit observer overhead of profiler.
2. Target a measurable increase in **committed source→fully ACKed projections/sec** without reducing SQLite FULL durability, weakening SHA/HMAC identity, skipping per-original validators, changing strict ordering, or masking stale output. Consider a bounded batch commit design for independently durable projections only with crash-consistency proof and exact per-event signed ACK receipts. The earlier naive `asyncio.to_thread` parallel observer idea was slower and must NOT be resurrected without new evidence.
3. Introduce a documented orphan owner-lock operator recovery protocol for actual SIGKILL/restart; replay and reconcile all previously committed ledgers. No automatic raw exchange history backfill or source continuity inference.
4. Re-run isolated synthetic benchmark + startup/crash/fault-injection CI. **Only** after a demonstrated performance margin, rerun public `3×1,000` with source freshness, backlog and queue acceptance and every single session independently PASS.
5. Phase 1 then separately requires P1-04 resilience, P1-05 truly continuous read-only feed and valid freshness over time, P1-06 verified-only runtime default cutover, P1-07 additional symbols/OHLCV and 24–72h real soak. The frozen provisional **80/100 engineering score** is not automatically promoted.

**Decision:** Phase 1 remains **OPEN**, PR #28 remains DRAFT, never merge main/enable REALITY/submit orders based on this pilot.
