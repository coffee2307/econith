# ECONITH Phase 1 — p95/p99 admission-age instrumentation and hard-exit forensic gate (2026-10-19)

**Latest status:** Two consecutive independent finite **3×1,000 Binance Public Spot in-session spool matrices PASS** using the explicitly opted-in SQLite observer connection-reuse path. The newer matrix includes independent per-session p50/p95/p99 histograms, exact observed maxima, fixed source→EventBus freshness cap, on-disk cursor/receiver/checkpoint reconciliation, and zero orders. This is **not** full continuous or production Phase 1 acceptance.

## New implementation

- `infrastructure/spot_projector_lag_histogram.py`: constant-space 50ms buckets and exact max/headroom plus backlog distribution. **Definition:** local original `received_at_ms` to the pre-EventBus verified publication admission gate. The age is recorded *after a successful publish*, but the sampled timestamp is **before** the EventBus publishing call. Thus this is **not network RTT, downstream delivery completion, consumer execution latency or exchange timestamp age**. p50/p95/p99 are nearest-rank **upper bounds of buckets** and may be slightly larger than exact max.
- `infrastructure/spot_forward_verified_research.py`: session-scoped histogram only in explicitly enabled spool research. Records on both success and failure into separate `projector_lag.json`, never the immutable original/ACK ledgers. No per-event payloads or identifiers. Real-time 5,000ms reject gate remains unchanged, with the existing second freshness check immediately before protected publishing.
- `ci/phase1_public_live_spool_trial.py`: complete successful finite source requires one histogram admission sample per independently verified receiver receipt, plus nonnegative headroom and a reported p99 bucket. `ci/phase1_public_live_spool_matrix.py` reports per-attempt percentile/headroom and quarantines incomplete source diagnostics without treating them as completed data.
- `tests/test_phase1_spool_physical_kill.py`: a **real child-process hard exit** via `os._exit(47)` after durable ACK/checkpoint and before projector cursor commit. WAL/FULL independent ledgers reopen; original source remains incomplete; cursor correctly trails; O_EXCL owner marker refuses an unreviewed recovery. This is a guarded synthetic fault-injection test, NOT an actual power-loss or geographically distributed failover certification.
- `ci/phase1_spool_forensic_inspect.py`: read-only CLI for one existing source/ACK/cursor bundle. Never removes owner locks, replays unverified originals or certifies HMAC cryptography. It distinguishes an owner marker **present** from confirmed orphan process liveness; marker presence alone never proves orphan status. It always reports `source_continuity_certified=false`.

## Directly observed new public evidence

One manually checked real Binance BTCUSDT 160-frame public pilot returned **PASS**, original=161, cursor=161, signed independent receiver ACK=152, chained checkpoint=152, FIFO peak=44/128, pending original peak=55, exact admission age max **2,105ms**, headroom **2,895ms**, histogram p50<=299ms, p95<=2,049ms, p99<=2,149ms; **0 orders**. Root on authorized Windows host: `F:\ECONITH-Phase1-Evidence\20261019_public_spool_lag_hist_160_v1`.

The subsequent fresh `3×1,000` matrix in `F:\ECONITH-Phase1-Evidence\20261019_public_histogram_3x1000_v1` returned exact verdict `PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY`, exit 0, **245.235s** elapsed. Each segment has separate public time preflight, WSS/REST, original/intent SQLite, HMAC independent receiver, outbox, primary+two named derived observers, checkpoint chain and separate cursor.

| Attempt | Original / cursor | Receiver ACK / checkpoint | FIFO peak | Pending peak | Admission age p50 upper | p95 upper | p99 upper | Max exact age | Minimum freshness headroom |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1,001 / 1,001 | 992 / 992 | 39/128 | 50 | 49ms | 1,349ms | 1,849ms | 2,047ms | 2,953ms |
| 2 | 1,001 / 1,001 | 992 / 992 | 59/128 | 93 | 49ms | 2,699ms | 3,299ms | 3,353ms | 1,647ms |
| 3 | 1,001 / 1,001 | 990 / 990 | 38/128 | 106 | 99ms | 3,449ms | 4,099ms | 4,197ms | **803ms** |
| **Total** | **3,003 / 3,003** | **2,974 / 2,974** | No observed overflow | Per-segment | No pooled percentile claimed | | | Under 5,000ms | 803ms worst |

An independent **read-only** SQLite + JSON postmortem on all 3 segments asserted all originals/cursors at 1,001, ACK equal checkpoints and histogram admission counts, `source_complete=true`, no original suffix and no remaining owner marker. No raw source was modified or uploaded. The earlier `3×1,000` passed on the same opt-in derived-observer optimization before the new telemetry existed; this second full pass establishes that the bounded histogram has not visibly broken finite acceptance.

Local selected testing after adding histogram, hard-exit and inspector achieved **46/46 PASS**. Complete target CI/Docker status must be checked at the final changed HEAD rather than inferred from earlier commits.

## Remaining gates and decision

**Phase 1 stays OPEN, provisional engineering score remains 80/100 pending frozen-matrix re-scoring.** Finite 3×1,000 PASS is not an Hour/Day or 24–72h soak, does not prove gap-free venue trade history, all-weather burst capacity, source validity after WSS reconnect, low tail-risk, full SIGKILL recovery, default verified-only ingress or multiple symbols/OHLCV. There is no automatic lock stealing, receiver ledger rewrite, source history repair, real-order authorization or main merge.

Next increments:
1. Establish a versioned **timed** research-soak acceptance separately from per-frame 1,000-run profile, with explicit stoppage and resource budgets, max/p95/p99 admission-age and backlog time-series, hourly/day-part coverage, and every session gap labeled UNKNOWN. A series of freshly opened sockets is NOT an uninterrupted market feed.
2. Add **operator-reviewed orphan owner recovery** with verified liveness and independently audited original/outbox/receiver/derived/checkpoint prefixes. Never automatically certify an incomplete source after power loss, stale WSS, or owner death.
3. Only after 1–2 and further load margin proof should consider runtime default verified-only cutover, then multi-symbol/OHLCV coverage and real 24–72h soak. Keep default SIMULATION, no execution/capital.
