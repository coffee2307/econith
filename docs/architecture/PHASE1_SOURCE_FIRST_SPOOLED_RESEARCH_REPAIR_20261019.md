# ECONITH Phase 1 — source-first durable spool repair (2026-10-19)

**Review scope:** bounded public Binance Spot BTCUSDT, research-only, separate opt-in path; PR #28 remains DRAFT. **Original institutional Phase 1 exit remains OPEN**, no default runtime switch or authorization for real orders.

## Incident being addressed

The original read-only forward pipeline waits within the one-frame-at-a-time ingress consumer for SQLite original COMMIT, original re-verify, source/book validator, producer outbox STAGE, independent signed receiver ACK, verified EventBus dispatch with 3 independently durable derived observers, final outbox ACK, and chained SQLite checkpoint. Bursty WSS arrival fills the fixed 128 FIFO before end-to-end event work finishes. Two previous 3×1,000 operator `Load` trials each scored 2/3 PASS, failed at **QUEUE_BACKPRESSURE 128/128**.

This repair does **not** weaken or replace the forward live in-session guarantees with a superficial queue increase.

## Implementation

New `infrastructure/spot_bounded_durable_spool.py`: the existing original `OfflineSpotCaptureJournal` with FULL/WAL SQLite and same-transaction `offline_forward_capture_intents` is the bounded **durable source spool**. During capture the callback performs only raw byte-budget accounting plus `IncrementalSpotShadowValidator.observe` of committed original bytes/Spot sequence/book. There are **no in-session EventBus publishers, HMAC receiver calls, or downstream SQLite ACKs** on the ingress path. Max 2,000 WS frames, max source raw 200,000,000 bytes, fixed queue capacity at most 128, fresh output path required. Every malformed, disconnected, clock-invalid, depth/aggTrade-gapped, overflowed or quota-exceeded source **fails closed**, with partial original evidence retained but NO downstream delivery.

After **successful original capture + sealed full source replay/book parity**, the existing `rescan_forward_eligibility` reconstructs strictly as-of source-bound envelopes. The already-reviewed `recover_staged_forward_spot` commits independent HMAC-authenticated receiver receipts, primary+named derived ledgers, producer outbox reconciliation, and chained checkpoints. All named research subscriptions are durable; every ledger has a separate owner/database. The new explicit `persistent_receiver=True` recovery flag reuses one HMAC-authenticated local subprocess, preserving **per-envelope independently COMMITted receiver ACK**, HMAC sequence and uncertain-ACK quarantine. The default recovery path remains unchanged.

New `ci.phase1_offline_burst_probe --sealed-spool` compares identical synthetic bursts without exchange access. New operator entrypoint `ci.phase1_public_sealed_spool_trial --allow-public-market-data` requires fresh output path, public time preflight, explicit read-only public-only source, no credentials/orders. This is intentionally **historical research delivery after source seal**, never claims `publication_in_session`, history continuity, low-latency live or default cutover. The legacy/default application is unchanged.

## Evidence directly executed on Windows test host

| Trial | Input | Verdict | Capture FIFO peak | Total elapsed |
|---|---|---|---:|---:|
| Prior old forward fast offline probe | 240 synthetic WS frames, 3ms spacing | FAIL / BACKPRESSURE | 128/128 | 7.20s |
| Initial sealed spool, 1-shot child per ACK | Same 240, 3ms | PASS, 241 originals / 239 ACKs | 2/128 | 51.38s |
| Sealed spool, persistent HMAC child | Same 240, 3ms | PASS, 241 originals / 239 ACKs | 2/128 | **13.61s** |
| Sealed spool, persistent HMAC child | **1,000 synthetic frames**, 3ms | PASS, 1,001 originals / 999 ACKs / 999 checkpoints | **2/128** | 69.38s (8.531s capture + 60.734s sealed replay) |
| One real Binance public TLS WSS/REST captured session | **1,000 real WS frames**, 120s source timeout | **PASSED_FINITE_SEALED_PUBLIC_RESEARCH_ONLY**, 1,001 originals / 993 signed receiver ACKs / 993 checkpoints | **42/128** | **135.47s** (71.391s capture + 62.422s replay) |

Real-source sealed run reports **1,341 derived receipts and 2,682 named receipts**, 525,527 raw original bytes, zero orders, `continuous_live_market=false`, `in_session_market_publication=false`, `history_continuity_verified=false`. Original data location on the operator computer: `F:\ECONITH-Phase1-Evidence\20261019_sealed_public_1000_v1`. This evidence has not been uploaded to GitHub; no originals are modified, moved, anonymized or reconstructed from unknown history. The command exited **0**.

Tests: Windows targeted `pytest` selection **36 passed** after public option and safety policy landed, plus 12 earlier spool/forward stage tests. CI `#39148358243`, container build `#39148358416` and smoke `#39148358248` all PASS at reviewed code commit `ed6ac6b41ec528414d900b868dfefce8c9a77d48`. Documentation follow-up commits require exact-HEAD CI checks.

## Explicit limitations and next gate

This repairs *bounded capture queue saturation caused by blocking on downstream acknowledgments*, at the cost of delaying all research outputs until the source has been sealed and replayed. It does **NOT** yet repair **continuous, in-session, low-latency verified market delivery** with lag limits, arbitrary consumer recovery, 24–72h soak, default-app market ingress cutover or broader institutional source coverage. Do not compare original old in-session `Load` 2/3 against this new *different* acceptance type and imply identical runtime promises; they are different workloads and deliverable scopes.

Next: design and validate a **continuously consuming disk-spool projection worker** with a bounded source→event lag and explicit stale/quarantine policy, durable per-consumer cursor, owner fencing and crash-injection. Measure real source read/commit rate and projection lag, not only capture FIFO occupancy; prove asynchronous downstream catch-up cannot publish old events as fresh. Re-run a 3×1,000 versioned continuous-in-session public acceptance **only after** evidence of this separate mechanism. Keep original source coverage unknown after any network outage, never pretend a fresh snapshot backfills trades. P1-04/05/06/07 and frozen engineering estimate **80/100** remain unchanged pending formal re-scoring and gate closure.
