# ECONITH Phase 1 — Bounded Binance Public co-running HMAC pilot design (2026-10-20)

**Read-only SIMULATION only, an experimental research branch.** This stage combines one real pinned Binance Public Spot BTCUSDT TLS WebSocket reader and the existing per-segment authenticated independent HMAC receiver while the next source journal is still accepting data. The research runner will never use a broker account, API credentials or order submission. Default `main.py` remains unchanged. Provisional Phase 1 exit remains **81/100**, and institutional Phase 1 is **OPEN**.

## Strict positive gate

1. GitHub Actions fetches immutable current commit tarball, runs offline public-opt-in/identity tests and full co-running crash fixture tests FIRST. Only push events from explicitly scoped research branch may attempt public network data. Pull-request jobs are offline only.
2. Public runner requires three CLI approvals: `--allow-public-market-data --allow-hmac-research --acknowledge-not-24h-proof`, one pinned Binance public factory, the existing independent public timestamp preflight, finite **90–150 second** monotonic window, **250–650** original WS frames per sealed SQLite journal and no automatic retry. The CI planned sample is **120 seconds, 450 frames per journal**. These are bounds, not minimum uptime promises.
3. Source and HMAC are co-running but different *tasks*: source lifetime reader -> FIFO 64 -> original SQLite FIFO 128 -> fsynced sealed journal; callback `put_nowait` -> independent <=2 segment backlog -> `asyncio.to_thread` HMAC child IPC -> durable receiver-owned ACK -> primary and two named read-only derived subscribers -> chained producer checkpoint. Only after a segment is sealed will that segment's HMAC work begin. No individual frame bypasses prior original capture, and no order-capable component is invoked.
4. A 5,000ms source first-receipt-to-original COMMIT maximum remains **unchanged**. The new source age histogram is `0–100ms, 101–500ms, 501–1000ms, 1001–5000ms`. Separate HMAC segment seal-to-checkpoint deadline is **25,000ms**. These are not exchange-to-user wire latency nor a per-frame end-to-end HMAC latency SLO.
5. Private journal disk budget **150MB**, one source socket, one lifetime reader, max 32 sealed journal segments, terminal WS silence <=5s, finite source frame count, and bounded worker queues are preserved. Capture report also records peak Linux RSS as well as user/system CPU. Any source event, FIFO overflow, unsynced order book, HMAC/observer timeout, original/hash mismatch, owner contention, or cap breach must quarantine rather than false-PASS.
6. After the source stops and the independent receiver drains, a read-only auditor opens every original SQLite journal and outbox/receiver/checkpoint/three subscriber SQLite ledger, recomputes raw SHA/projection hashes/checkpoint chain, checks histogram count/aggregate totals and resource caps, and confirms zero orders. The tested live result is `PASSED_BOUNDED_PUBLIC_SPOT_CORUN_HMAC_RESEARCH_ONLY` only when all local source and downstream gates pass.

## Forensic collection

GitHub CI uploads `aggregate_report.json`, `hmac_corun_aggregate.json` when available, sanitized quarantine and independent audit JSON **only**. Original raw trade/depth frames, raw SQLite, session keys and IPC authentication are not uploaded to public workflow artifacts. Aggregate hashes alone do not allow a third party to reproduce the original source history; operator-held private raw retention remains a separate required condition for institutional assessment.

## Negative claims

- **Not per-frame HMAC forwarding.** The sealed segment HMAC stage only overlaps future raw capture, not the segment before its seal.
- **Not 24h**, not exchange-wide historical completeness, not verified exact Binance emission/receipt parity, no producer-to-subscriber universal EventBus transactional guarantee, no multiasset verification, no distribution-safe lock, no hardware power cut, and no approved production verified-only cutover.
- Time-bounded public positive evidence would be one engineering preflight for P1-05, **not enough to award P1-05 15/15 or Phase 1 100/100**.

## Reviewed next stages

On successful pilot: profile independent source-to-original and segment-seal-to-ACK metrics under burst/fault workloads; qualify source/receiver hard kill and controlled recovery with private original evidence; add truly continuous per-frame HMAC if required by specification and resource-safe 24h–72h persistent services only after an operator-reviewed gate.
