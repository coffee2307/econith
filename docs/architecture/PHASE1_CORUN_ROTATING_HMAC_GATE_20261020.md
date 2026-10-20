# ECONITH Phase 1 — Co-running source reader and per-segment authenticated receiver gate (2026-10-20)

**Status: draft, offline-only research, no public Binance or real order calls.** Extends already independently audited single TLS source / 9-segment raw rotation and post-seal receiver checkpoint research. Intentionally limited to each segment's *post-seal* delivery while the **overall source reader remains attached**. It does not implement per-event streaming HMAC or institutional 24/72h proof.

## Isolation and backpressure

- One source reader, one 64-frame fail-closed raw FIFO and existing 128-frame writer FIFO. The experimental optional `on_segment_sealed` hook runs only in injected fixture mode after a journal has been fsynced and closed, and uses `put_nowait` into an independent capacity-2 segment backlog. On overflow, HMAC child failure, invalid source or missing local checkpoint the global run is quarantined, not partially PASS.
- One consumer worker processes sealed segments in order. The existing `recover_staged_forward_spot` authenticates an independent receiver subprocess and seals original→outbox→receiver→derived/named observers→checkpoint after each segment. That downstream work runs via `asyncio.to_thread` with an independent loop so blocking SQLite and IPC cannot starve the source reader's loop. This does **not** prove that the kernel/network library never buffers market frames, or that external exchange events are complete.
- At monotonic source end, the still-owned global capture scope drains the bounded sealed-segment queue, verifies source success and every downstream parity, and writes a global co-run aggregate. A dedicated read-only cross-ledger inspector re-verifies all local original/intents, projection fingerprints, ACK receipt IDs, checkpoint chain, per-segment source identities and closed source session, without reading HMAC ephemeral wire keys.
- `segment_handoff_lag_max_ms` is measured *from journal seal to completed derived/checkpoint* (includes segment queue lag), bounded at 20,000ms by default. It is **not** an individual WS event-to-ACK latency guarantee, and it does not borrow the separate 5s event-to-original COMMIT gate.
- Only a finite operator-approved fixture duration and bounded frame limits are allowed in this iteration; experimental callback cannot be enabled by public-facing CLI. No credential/API/trading side effects, real source losslessness or production default ingress changes.

## Adversarial proof scope

- Refuse absent research opt-in, injected HMAC worker failure, segment backlog overflow, old/downstream ambiguous ledgers or tampered original/checkpoint (depending on test).
- Physical Linux `os._exit` after independent HMAC receiver SQLite COMMIT but before producer outbox ACK **while the source reader is still active**; orphan global+segment ownership markers, no false complete aggregate and no automatic takeover.
- Post-crash recovery remains an operator-reviewed task. This test does NOT clear orphan owner markers or silently repair an uncertain in-session HMAC suffix.

## Boundaries

Full 24h resident service, venue completeness, distributed fencing, hard electrical power loss and fully continuous per-frame HMAC submission are still unproven. Provisional frozen exit score **81/100**; P1-05 13/15 until sustained real-source HMAC pipeline and independent 24–72h operator soak. P1-06 default cutover and P1-07 multiasset completeness remain OPEN.