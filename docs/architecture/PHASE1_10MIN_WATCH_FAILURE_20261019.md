# ECONITH Phase 1 — Conservative 81/100 re-score and failed 10-minute timed public research watch

**Date:** 2026-10-19. **Status:** Phase 1 **OPEN**; no production, real orders or default app cutover. PR #28 stays DRAFT.

## Frozen matrix re-score

The weighted **institutional** Phase 1 denominator is **unchanged** from `PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md`. Conservative engineering evidence credit moves from **80/100** to **81/100 provisional** on P1-05 (+1), based on two independent public Binance 3×1,000 **finite** in-session spool matrices with p95/p99, bounded lag, independent HMAC receipt/checkpoint/cursor reconciliation, and real hard-exit fault injection. Gate credits: P1-01 20/20, P1-02 15/15, P1-03 15/15, P1-04 14/15, P1-05 **13/15**, P1-06 4/10, P1-07 0/10. No new gate, shortened denominator or 100% claim. See `PHASE1_WEIGHTED_RESCORE_20261019.md`.

## Implementation of next gate (finite monitored watch)

New `ci/phase1_public_spool_timed_watch.py` introduces a **manually opted-in** 10-minute observation watch with a hard 15-minute ceiling, at most 16 separately opened public Spot WSS/REST sessions, max 1,000 frames/segment, 120s source timeout/240s entire segment watchdog, fixed 5s admission freshness cap, 128-frame queue, max 512 pending original records, independently HMAC-signed receiver, three separately durable cached FULL-commit observers, cursor and hash-chain checkpoint, zero orders. The app is never imported at default startup. Separate original capture data and completed/failed diagnostics are left immutable.

It checks Windows physical available RAM via `GlobalMemoryStatusEx` with no external `psutil` dependency (min free **1,000,000,000 bytes**), disk free >=**2GB**, and total trial evidence <=**500MB**, before/after each segment. Resource-probe failure denies the next source. `watch_report.json` is fsync/atomic-replaced with durable `STARTED_NOT_CERTIFIED` before each network attempt, and only changes to PASSED when original/ACK/checkpoint/cursor/histogram counts and age/freshness invariants agree. One failed segment ends the watch; there are **NO blind retries to obtain a prettier pass ratio**. Reopened public connections ALWAYS add an **UNKNOWN** history coverage boundary. The explicit CLI flag accepts those unverified gaps, not fake historical completeness. Unit tests cover cumulative target, failure, resource denial, malformed acceptance and bounds.

This pilot is NOT proof of one uninterrupted WSS, and no 24–72h acceptance can be borrowed from its elapsed wall-clock time.

## Real host 10-minute watch outcome: **FAILED, stopped after 396.218s**

Evidence root on authorized Windows host:
`F:\ECONITH-Phase1-Evidence\20261019_public_spool_timed_watch_10min_v1`

Four 1,000-frame independently sealed public Binance sessions PASS. Fifth session was **FAILED_OR_QUARANTINED**, reason `FORWARD_PROJECTOR_SOURCE_LAG`; final program exit **1**, `target_reached=false`, elapsed **396.218s**, NOT 600s.

| Attempt | Result | Duration | Raw original | Cursor | Durable receiver ACK | Chained checkpoint | p99 bucket upper | Max admitted age |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | PASSED finite | 89.187s | 1,001 | 1,001 | 990 | 990 | 1,249ms | 1,349ms |
| 2 | PASSED finite | 92.031s | 1,001 | 1,001 | 990 | 990 | 1,599ms | 1,767ms |
| 3 | PASSED finite | 83.953s | 1,001 | 1,001 | 990 | 990 | 4,349ms | 4,456ms |
| 4 | PASSED finite | 78.766s | 1,001 | 1,001 | 977 | 977 | 2,249ms | 2,403ms |
| 5 | **FAILED / SOURCE LAG** | 52.234s | **738 partial** | **646** | **638** | **637** | **4,749ms incomplete** | **4,974ms last admitted** |

The failed source contains **92 durable originals beyond the cursor** and **1 authenticated receiver ACK beyond the chained checkpoint**. There are **637 recorded admission-age samples** and 638 receiver receipts, correctly preventing a complete-source acceptance claim. The next stale event was denied at the freshness gate. No source rows, ACKs, checkpoints, receiver evidence or owner locks were rewritten. The failed histogram has `source_complete=false`; it is **forensic only**. All five sources were closed and no owner fence remained, but postmortem read-only evidence is *not* a cryptographic HMAC revalidation. The watch contains **four explicitly UNKNOWN intersegment market-history boundaries**.

### Host resource observations and uncertainty

Host started with about 2.22 GB physical free RAM and 76.39 GB free disk before the watch. Per-segment RAM available **after** attempts 1–4 was ~2.65GB, ~2.65GB, ~2.58GB, then **1.10GB**, near the deliberate 1GB stop threshold. The failed attempt began at ~1.10GB free. It failed on the **freshness gate**, not a detected resource threshold. No failed-attempt after-sample exists; **do not assert RAM pressure caused source lag** without cross-sampled memory/IO/CPU input-burst traces. Total research evidence after four sessions was ~33.7MB, below the 500MB limit. The host has not been stressed again after the abort.

### Conclusions and next increment

The sustained 10-minute watch is **NOT PASSED**; 81/100 evidence-credit remains provisional and unchanged after the aborted watch. Maintain 5s cap and per-event FULL durability. Before retrying, improve observability of source ingress arrival rate, projector service time per original, independent SQLite fsync latency/CPU contention, and resource headroom around the stale incident. Cross-check time-series burst lag and Windows page pressure with signed/no-secret diagnostic aggregates. Add a separate operator-reviewed crash/orphan reconciliation procedure; never steal an active/unknown producer marker and never infer missing trades across unknown WSS intervals. **Do not** restart a new 10-minute test until the measured bottleneck is addressed and host resources are adequate. P1-06 default market-source cutover and P1-07 multi-symbol/OHLCV + real 24–72h operator evidence remain OPEN.
