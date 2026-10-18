# ECONITH Phase 1 — causal spool-pressure instrumentation and cached producer benchmark (2026-10-19)

**Scope:** Draft PR #28 / experimental read-only SIMULATION. No main merge, live orders, real broker accounts, production cutover or full institutional Phase 1 exit. Frozen score remains **81/100 provisional engineering evidence**, not a trading/reliability probability. The original 10-minute timed watch remains **FAILED after 396.218s**: 4/5 accepted bounded segments; fifth quarantined on `FORWARD_PROJECTOR_SOURCE_LAG` with 92 original rows still past the cursor.

## Root-cause refinement — per-window original versus projection service rate

New opt-in `infrastructure/spot_spool_pressure_diagnostics.py` records at most 48 aggregate **5s** intervals of post-original-commit callback arrivals, cursor-committed completions, verified EventBus publications, downstream checkpoint completions, backlog maximum, pre-publication admission-age maximum, process CPU time and Windows available physical memory (native `GlobalMemoryStatusEx`, no psutil dependency). `spool_pressure.json` and existing `stage_latency.json` are written only to the isolated new output directory, including on failures; no market frames, credentials, absolute host paths, per-event identifiers or wall-clock sample times are copied. This is **diagnostic-only**, not an independently authenticated source record.

Offline `240` synthetic WS frames spaced `20ms` with existing separately durable derived observer connection reuse, BEFORE further producer optimization:
- Five-second bucket 0: **216 ingress original callback records vs 104 durable cursor-completed originals** (43.2/s vs 20.8/s), backlog peak 112;
- Five-second bucket 1: only 25 new original callbacks vs 137 cursor completions; backlog peak 126; source eventually seals with 241 originals, 239 receiver ACK/checkpoints, input FIFO peak 2/128, max age **4,201ms**.
- Stage profiler identified repeated producer outbox STAGE, receiver ACK update, derived ACK update and checkpoint COMMIT/open overhead, alongside source SQLite COMMIT and persistent HMAC IPC. These are cumulative separately timed operations; sums must not be misrepresented as wall-clock latency.

First **real public** 160-frame pressure telemetry with existing derived reuse (before new producer caching): **PASS** with 161 originals, 150 HMAC ACK/checkpoints, cursor 161, max lag 86ms, ingress queue 2/128 and backlog peak 3. Measured source input around **9–12 originals/sec**, so this calm public interval does **not** falsify the synthetic burst bottleneck. Root: `F:\ECONITH-Phase1-Evidence\20261019_public_pressure_160_v1`.

## Experimental change: per-session cached producer outbox/checkpoint connections

The producer outbox and checkpoint ledgers each previously reopened SQLite several times per eligible projection. Both now have **optional `reuse_connection=True`** with explicit end-of-session closing, strictly one session/owner, each in its own SQLite database. Their original `BEGIN IMMEDIATE`, `COMMIT` **per state transition** and `WAL` / `synchronous=FULL` are intact; no batch acknowledgement, skipping raw originals, HMAC bypass, checkpoint chain shortcut or relaxed freshness gate. `observe_forward_spot_research(..., experiment_reuse_producer_connections=True)` is denied unless spooled research is explicitly enabled together with reviewed derived-cache mode; public use additionally requires the finite public experiment policy. The CLI adds a separate `--reuse-producer-connections` switch; normal/default operation does **not** enable it.

Two repeated sequential offline A/B comparisons of the identical fixture shape (240 WS at 20ms) on the same Windows host, with all other simulated forward semantics unchanged:

| A/B pair | Baseline total time | Cached producer total time | Baseline max admission-age | Cached producer max age | Baseline checkpoint mean | Cached checkpoint mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 10.22s | **6.67s** | 4,036ms | **2,142ms** | 5.952ms | **0.607ms** |
| 2 | 10.20s | **6.76s** | 4,286ms | **2,163ms** | 5.913ms | **0.592ms** |

All four runs had 241 original/cursor positions, 239 authenticated receiver ACKs and 239 chained checkpoints, exit 0 and no orders. Aggregate elapsed time improvement in these paired offline cases is **~34%**, not a guarantee for all live market conditions. Mean outbox stage fell from ~2.8–3.0ms to ~0.7ms. HMAC receiver remained ~7ms mean and requires separate investigation.

Safety regression tests validate producer WAL/FULL even while connection cached, independently owned outbox and checkpoint SQLite handles, absence of implicit public/derived opt-in, and actual child-process `os._exit(47)` at committed checkpoint→uncommitted cursor for both uncached/cached producer variants. Such abrupt shutdown **cannot** certify an interrupted public market source or gap-free continuity.

## Bounded public Binance checks of new experimental variant

- 160 actual public WS frames with original REST snapshot: PASS, **161 original/cursor**, **152 HMAC receiver ACK/checkpoints**, 348 named and 174 primary derived receipts, ingress peak 6/128, backlog peak 7, exact local source→EventBus admission age max **162ms** and p99 bucket upper **149ms**, zero orders. Evidence root `F:\ECONITH-Phase1-Evidence\20261019_public_cached_producer_160_v1`.
- One **1,000 real Binance WS frame** bounded finite session: **PASS, 1,001 originals/cursor, 992 HMAC ACK/checkpoints**, authenticated derived observer reconciliation, in-session publication, 0 orders, ~70.22s whole process. **Important: queue peak 123/128, pending originals peak 163, admission age max 4,586ms, p99 bucket upper 4,399ms and freshness headroom only 414ms**. Root `F:\ECONITH-Phase1-Evidence\20261019_public_cached_producer_1000_v1`.
- At seconds 30–35 in that public 1,000-frame run, **211 new original callbacks vs 150 cursor completions** (~42.2/s vs 30/s) while pending backlog peaked 163 and local source admission age rose into multi-second range. The next five seconds saw 89 new original callbacks vs 174 completions as the transient backlog was consumed. This reproduces a throughput limitation **despite** producer caching. Worst observed HMAC ACK wait on this single public run was ~1,093ms, but attribution to RAM, IPC scheduling, disk write or network traffic is not yet demonstrated.
- Windows RAM varied during these observations; other previously running applications were NOT stopped or modified. An observation of low available RAM is **not** evidence of causation; system-level CPU/IO/wait breakdown and repeated comparable public burst profiles remain unmeasured.

## Decision and next work

**Do not run an automatic new 10-minute soak or declare P1-05 CLOSED.** The new cached producer mode materially reduced offline stage overhead and passed one finite public 1,000-frame trial, but input FIFO was **123/128** and admission freshness was within **414ms** of the 5s cutoff. The old failed 10-minute watch remains a valid failure, not overwritten by a fresh short PASS. Do not lower `synchronous=FULL`, enlarge queues to hide loss, extend stale budgets or silently replace legacy default market ingress.

Next should instrument **source writer/reader queue pressure**, HMAC independent receiver p99 outliers and Windows process/disk stalls in time windows before further optimization; repeat a controlled public load only with headroom and resource preflight, then re-attempt bounded 10min observation. Separately, P1-04 reviewed owner-fenced recovery, P1-06 verified-only default cutover and P1-07 multi-symbol/OHLCV/24–72h remain unaccepted. PR #28 stays DRAFT.
