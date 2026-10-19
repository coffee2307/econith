# Phase 1 — first real 3 × 1,000-frame Load acceptance: backpressure persists (2026-10-18)

## Scope and provenance

This is an operator-host observation on Windows against **public, read-only Binance Spot BTCUSDT**. It is not CI, a broker/order test, a 1-hour success, or evidence of uninterrupted history. An assistant remotely executed the operator `Load` profile and independently ran the existing read-only report plus aggregate SQLite queries. Full source evidence stays on the operator's computer and has **not** been uploaded to GitHub.

- Reviewed Draft PR #28 checkout fast-forwarded from `7f3300ec` to `e21e59be02e9d117474bc4582aa820a9dc9c5f83`. Untracked vendor directories were left untouched.
- Local `pytest tests/test_phase1_operator_lab.py tests/test_spot_forward_supervisor.py -q`: **24 passed**, exit 0.
- Doctor before the test: Windows 10, Python 3.12.10, 4 logical CPUs, **2.56 GiB free RAM**, **71.35 GiB free disk**, required Python imports available.
- One explicit `-Mode Load` run created **new** trial `20261018T155526Z_load_77f36eb6` outside the repository. Three independently sealed attempts, up to 1,000 public WSS frames and 120s each, fresh WSS and REST snapshot for each, one persistent authenticated HMAC receiver per session, 200MB evidence ceiling, fixed 128 FIFO. No API key or orders.
- Child exit code **1** after 199.52 seconds. `report.json` verdict **FAILED_OR_QUARANTINED_SOURCE**.

## Directly observed durable aggregate evidence

| Attempt | Status | Persisted original source rows (read-only count) | Complete producer outbox rows (DERIVED_ACKED) | Ingress queue peak | Supervisor attempt duration (includes preflight) |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | PASSED | 1,001 | 995 | 38/128 | 94.58s |
| 2 | **FAILED** | **279, incomplete session** | **273, incomplete proof** | **128/128** | 21.67s |
| 3 | PASSED | 1,001 | 994 | **126/128** | 79.69s |

The original supervisor report correctly counts **2,002 original rows and 1,989 independent receiver ACKs only from the two PASSED attempts**. Failed attempt source rows/outbox entries are forensic evidence, **NOT certified acceptance**. The failed attempt's sanitized, durable failure class is `SPOT_TRANSPORT_QUEUE_BACKPRESSURE`; this is not a CLOCK PREFLIGHT failure. Maximum receiver ACK latency **32ms** applies to PASSED attempts, not to the failed attempt. Minimum sampled available host RAM **1.771 GiB** across 66 observational samples. There is **one restored outage** and **two unknown between-session history boundaries**. Orders submitted: **0**.

Read-only arrival-second grouping of **persisted** originals showed busiest observed seconds of 52, 68 and 87 rows for attempts 1–3. These are *not* complete raw WebSocket arrival rates when the FIFO has overflowed. In particular, attempt 3 handled a higher single-second observed count but narrowly reached queue depth 126/128. This makes `max frames/s` alone an inadequate root-cause explanation. Frame type, cumulative I/O/ACK latency, session burst length and scheduler behavior remain candidates, not proven causes.

## Gate decision

**FAIL**: 2/3 finite Load segments passed; one FIFO saturation quarantined its whole source. Never revise the failed source into a pass using later recovery, synthesize absent trades, mark unknown boundaries recovered, or retry Hour/Day now. The prior public-clock 5/5 short probe does not exclude later HTTPS timeouts. This new result is a different, now reproduced backpressure failure.

**Next investigation (no repeat public Load yet):**
1. Add a narrow, opt-in, bounded **per-stage latency profiling** path for source SQLite append+re-read, incremental book verifier, outbox stage/ACK, authenticated child receiver round trip, EventBus + derived durable subscribers, and checkpoint COMMIT. Persist only aggregate monotonic duration histograms/counters to separate diagnostic output, including on failed sessions; no raw frame contents, credentials, or host paths. Do not modify immutable source/outbox ledgers or admit incomplete source.
2. Build a deterministic offline burst benchmark that includes different aggTrade/depthUpdate ratios and asserts that a saturated 128 queue always fails closed, not drop/reorder/silently retry.
3. Optimize only the bottleneck substantiated by profiling while preserving source-first durability, mandatory signed independent receiver ACK, verified derived receipts, checkpoints, freshness, bound 128 and outage semantics.
4. Rerun targeted CI/regression checks before **one new** operator `Load` trial; compare queue peaks, latency histograms, successful source/receiver/derived/checkpoint counts and sampled RAM. Three passes still do not establish gap-free/continuous Hour/Day acceptance.

No changes to default app source, real trading mode, production deployment, merge status or capital gates. Keep PR #28 **DRAFT** and institutional Phase 1 **OPEN**.
