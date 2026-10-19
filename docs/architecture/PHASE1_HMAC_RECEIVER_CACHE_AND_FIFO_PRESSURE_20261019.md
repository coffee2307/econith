# ECONITH Phase 1 — independent HMAC child receiver SQLite cache and burst-window telemetry (2026-10-19)

**Research-only engineering record.** Phase 1 institutional exit remains **OPEN** and provisional evidence credit **81/100** (frozen 7-gate matrix unchanged). No production, orders, capital, default ingress switch, 10-minute re-soak, historical coverage certification or PR merge.

## What was measured

Prior 1,000-real-frame opt-in Binance pilot with cached *producer* and derived observers passed 1,001 originals/cursor and 992 independent signed receiver/checkpoints, but was near both limits: ingress queue **123/128**, original backlog **163**, lag peak **4,586/5,000ms** and a receiver HMAC ACK worst case ~**1,093ms**. Historical 10-minute multi-session watch remains **FAIL at 396.218s**, with 4 finite segments accepted and fifth quarantined on `FORWARD_PROJECTOR_SOURCE_LAG`. Do not overwrite that verdict.

Investigation found the separately running, persistent authenticated **receiver child process** was opening and closing its own independently owned SQLite connection for every signed envelope, despite using `WAL`/`synchronous=FULL` and `BEGIN IMMEDIATE → COMMIT` per envelope.

## Experimental changes — no default behavior modifications

- `OfflineIndependentProjectionReceiver(..., reuse_connection=True)` optionally retains a *receiver-child-owned* connection during one finite research source. Still separate from producer original, outbox, derived and checkpoint databases; same strict envelope validation, SHA fingerprint and canonical JSON, same event identity/sequence, SQLite FULL durable COMMIT per envelope, and idempotent/conflicting replay behavior.
- `ci.phase1_persistent_receiver_worker --reuse-receiver-connection` is supplied only when an explicit `PersistentHmacResearchReceiver(...,reuse_receiver_connection=True)` requests it. No new IPC protocol, network socket, partial ACK promotion or secret exposure.
- `observe_forward_spot_research(...,experiment_reuse_receiver_connection=True)` is fail-closed unless **all** bounded spool, independent receiver, derived observer caching and producer caching conditions are met; public network additionally requires the same existing finite public approval gate. `ci.phase1_offline_burst_probe --reuse-receiver-connection` and `ci.phase1_public_live_spool_trial --reuse-receiver-connection` are both explicit opt-in. **Default false**, timed watch does not enable it.
- Existing optional `BoundedSpoolPressure` five-second window diagnostic now also captures ingress FIFO **per-enqueue depth peak and count of samples at >=90% capacity** plus HMAC ACK round-trip peak and counts over 50/250/500ms. Queues remain max 128 in the public spool; these diagnostics do not copy payloads, timestamps, credentials or replace original HMAC/ACK evidence.

## Offline reproducibility

Two sequential A/B comparisons each used 240 synthetic WS frames at 20ms spacing, same existing cached derived observers and outbox/checkpoint producers; only the child receiver DB cache was varied:

| Run | Uncached child | Cached receiver child |
| --- | ---: | ---: |
| 1 elapsed | 6.41s | **6.25s** |
| 2 elapsed | 6.58s | **6.28s** |
| 1 receiver HMAC ACK mean | 7.317ms | **5.404ms** |
| 2 receiver HMAC ACK mean | 7.310ms | **5.600ms** |
| 1 receiver ACK p99 | 19.018ms | **10.950ms** |
| 2 receiver ACK p99 | 19.470ms | **12.189ms** |
| 1 maximum admission age | 1,942ms | **1,697ms** |
| 2 maximum admission age | 2,056ms | **1,718ms** |

All four independently sealed 241 original/cursor positions and 239 ACKs/checkpoints. Receiver ACK mean dropped about **25%** for this fixture, total wall duration about **3.5%**. Do not attribute a numerical improvement under actual exchange bursts without repeated comparable public sessions.

Additional offline 240-frame full-cache/diagnostic test PASS: elapsed 6.16s, original and cursor 241, signed ACK/checkpoint 239, max admission age 1,664ms, input FIFO 2/128, original pending backlog peak 106. During 0–5s observed 241 callback arrivals vs 200 cursor completions, FIFO peak 2, one receiver ACK over 50ms (observed maximum 172ms), none over 250ms, and no >=90%-capacity FIFO observation. It confirms downstream backlog distinct from input FIFO saturation in this sample.

## Bounded actual public Binance verification

One fully opted-in fresh public Spot **160 WS frame** research source PASS with cached receiver+producer+derived ledgers, public clock preflight, own signed receiver child, 161 originals/cursor, 151 HMAC receiver ACKs/checkpoints, 370 named and 185 primary derived receipts, queue peak **9/128**, unprojected pending peak 11, admission-age maximum **236ms**, p99 upper bucket 249ms, freshness headroom 4,764ms, **0 orders**. `session_complete=true`, `history_continuity_verified=false`, `continuous_live_market=false`. Physical evidence path on authorized Windows machine: `F:\ECONITH-Phase1-Evidence\20261019_public_receiver_cache_160_v1`. This is **not** a continuous feed, 1,000-frame burst tolerance proof or long-duration soak.

Tests enforce actual HMAC child ACK (not a mock), SQLite journal WAL and synchronous FULL for cached receiver, independent cursor/receipt integrity, fail-closed policy, as well as **real subprocess os._exit(47)** after a durable checkpoint but before original cursor update with receiver cache on, proving incomplete source quarantine and owner-fence preservation. No automatic owner takeover, gap repair or phantom ACK.

## Current interpretation and next step

The incremental child cache helps but does not yet explain one-off HMAC round trips >1 second or the near-overflow input FIFO during real 1,000-frame burst. Prior public burst rate (211 original arrivals vs 150 cursor completions in a five-second window) remains the strong demonstrated source/service-rate mismatch. The newly added diagnostic correlates high FIFO occupancy with ACK stall windows, but **has not yet been exercised under a comparable real public burst**. First inspect Windows host headroom and process/SQLite stalls, prove safe profile in bounded repeat trials, and consider a controlled 1,000-frame/3×1,000 validation ONLY with sufficient headroom and unchanged 5,000ms freshness cap; keep 10-minute and 24–72h soak blocked until repeated burst safety margins exist.

Primary previous investigation: [PHASE1_PROJECTOR_PRESSURE_AND_PRODUCER_REUSE_20261019.md](PHASE1_PROJECTOR_PRESSURE_AND_PRODUCER_REUSE_20261019.md). No trading permission is implied by these changes.
