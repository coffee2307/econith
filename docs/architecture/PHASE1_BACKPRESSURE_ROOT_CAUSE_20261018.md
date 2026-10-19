# ECONITH Phase 1 — diagnosed queue saturation on real Binance Spot (2026-10-18)

**Status: ROOT CAUSAL PATH ESTABLISHED; PERFORMANCE REMEDIATION OPEN.** This is a read-only public-market RESEARCH finding, not continuous/live-market certification, not trading authorization and not a retrospective rewrite of FAILED source evidence.

## 1. Real repeat with opt-in stage profiling

Reviewed local feature checkout `411054ea7c563b94953335b7ca55fc5105872310` (follow-up report-only commits do not change source capture). New evidence root, never reused: `F:\ECONITH-Phase1-Evidence\20261018T164057Z_load_0eb97efa`.

Explicit one-time `-Mode Load`: three fresh Binance public WSS + REST sessions, up to 1,000 frames/120s each; 128-slot fail-closed FIFO, authenticated persistent receiver and independent original/outbox/derived/checkpoint SQLite ledgers. Runs SIMULATION-only and submits **zero orders**.

| Attempt | Classification | Raw persisted original SQLite rows | Accepted/complete supervisor originals | Receiver ACKs counted for accepted sessions | Queue peak | Supervisor duration |
|---|---|---:|---:|---:|---:|---:|
| 1 | PASSED | 1,001 | 1,001 | 993 | 119/128 | 74.42s |
| 2 | **FAILED / SPOT_TRANSPORT_QUEUE_BACKPRESSURE** | **870 incomplete** | **0** | **0 certified** | **128/128** | 60.21s |
| 3 | PASSED | 1,001 | 1,001 | 995 | 52/128 | 74.09s |

Aggregate **2/3 PASS**, process exit **1**, verdict `FAILED_OR_QUARANTINED_SOURCE`. The 870 failed-source originals and 864 partial producer/receiver/derived checkpoint stages are *forensics*, not a sealed source or a valid trading signal. The two accepted segments total **2,002 originals / 1,988 durable independent receiver ACKs**. Outage window was closed by the next valid session, but **two between-session history boundaries remain UNKNOWN**. Minimum sampled host available RAM **2.22 GiB** across 70 samples. No conclusive evidence of physical RAM exhaustion.

In FAILED attempt 2, 864 profiled accepted-forward event stages produced:
- `eventbus_publish`: **11,304 ms total**, p95 **15.375 ms**, ~13.08 ms mean.
- `checkpoint_commit`: **5,118 ms total**, p95 **6.961 ms**, ~5.92 ms mean.
- `derived_subscriber_wait`: **5,043 ms total**, p95 **13.781 ms**, ~5.84 ms mean.
- `receiver_hmac_ack`: **4,160 ms total**, p95 **5.644 ms**, ~4.81 ms mean.
- `source_sqlite_commit`: **3,941 ms total** over 870 persisted rows, p95 **5.615 ms**, ~4.53 ms mean.

These are individual *monotonic stage* durations; totals must not be conflated with end-to-end wall time or assumed independent because async operations can overlap. `EventBus publish` includes handler-side work; internal subscriber timings are not yet isolated per SQLite file.

## 2. Independently reproduced throughput mechanism (offline synthetic)

The `ci.phase1_offline_burst_probe` uses a fresh source, synthetic internally generated Binance-format frames, actual source SQLite original commits, one OS HMAC receiver, protected EventBus, three durable derived observers and per-event checkpoints. No exchange connection, user API key or trading.

- 240 frames with 3ms input spacing: **SPOT_TRANSPORT_QUEUE_BACKPRESSURE**, queue **128/128**, quarantined source, exit 1. Repeated successfully with bounded aggregate profiling and safe failure-code output.
- Same 240-frame sequence with 60ms input spacing: **BOUNDED_OFFLINE_PASS**, 241 original (including fresh REST fixture snapshot), 239 durable receiver receipts, queue peak **2/128**, elapsed 13.48s. No claim of real public provenance.
- Experimental `asyncio.to_thread` parallel writes for the three independently durable observers at the 60ms setting **underperformed**: elapsed 16.31s versus baseline 13.48s; `eventbus_publish` got shorter but `derived_subscriber_wait` grew. The entire thread-offload experiment was reverted. It is NOT enabled in the public operator profile.

**Causal path, established by code and controlled tests:** the single original FIFO consumer awaits `on_persisted(record)` before dequeuing another original. That callback serially waits for as-of validation, producer outbox STAGE, HMAC receiver durable ACK, protected EventBus/three derived ledger receipts, outbox ACK and another FULL SQLite checkpoint commit. When raw receipt rate exceeds this end-to-end service rate for a sustained burst, the 128-slot FIFO must saturate and the source is intentionally failed closed. Both offline and public WSS observations confirm this. The precise fraction of the public-network failure attributable to event burst shape, disk fsync jitter, subscriber CPU or host scheduler is **not** separately proved; per-subscriber and instantaneous input/output rate profiling remain optional follow-up refinements.

## 3. Safe architectural remediation, not yet implemented

Do **not** weaken synchronous/FULL durability, remove HMAC/subscriber/checkpoint evidence, raise FIFO merely to hide loss, replay unknown market history, synthesize missing aggTrades, or claim that consecutive bounded sessions represent continuous venue coverage.

The next major performance change should be reviewed as a separate increment:
1. Introduce a **source-first append-only durable spool** that commits the original raw event and immutable source receipt promptly, independently of slow downstream observers, with a fixed disk-byte quota, generation/owner lease, identity and ordered offsets. Never release consumer admission based on mere in-memory enqueue.
2. Use an independent, bounded, restart-recoverable projection worker with durable per-consumer offsets and exact original→derived receipt reconciliation. Duplicate/offline processing must be idempotent, and incomplete suffix/UNKNOWN must remain quarantined.
3. Record **raw-to-derived delivery lag** and bound it; a delayed derived event must never be falsely advertised as fresh live market data. Stop/deny publication when validity/max-age is breached; keep raw evidence for later as-of research replay instead.
4. Benchmark this split under deterministic sustained load and burst profiles on the same host, including SIGKILL between raw commit / consumer checkpoint, disk-full, worker stall, reordered frames and HMAC mismatch. Preserve buffer/backpressure limits on **each** internal stage.
5. Then run one new public `Load`, require all 3 segments PASS with safety evidence and reasonable queue/lag headroom. Subsequently separately address genuine continuous source, default verified-only runtime cutover, multi-asset/OHLCV and operator 24–72h soak.

New aggregate profiler module: `infrastructure/spot_forward_stage_profile.py`; conditional activation in `ci.phase1_operator_lab` **Load** only, output `attempt_NNN/stage_latency.json` separate from immutable source DBs; `ci.phase1_operator_lab report` summarizes top stages without changing source verdict. Only aggregate bounded metrics are saved; no frames, credentials, URLs or host paths within profiler files.

**Exit gates:** P1-05 remains failed/incomplete in real load. P1-04, P1-06 and P1-07 remain separately open as documented in `PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md`. The original provisional **80/100** weighted engineering estimate is not a gate pass or % reliability. Formal Phase 1 100%/production/live trades remain BLOCKED. PR #28 stays DRAFT.
