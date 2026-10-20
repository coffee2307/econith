# ECONITH Phase 1 — Co-running rotating source + HMAC independent receiver: offline acceptance (2026-10-20)

**Outcome:** Research-only offline fixture qualification PASS; provisional weighted Phase 1 **81/100 UNCHANGED**. Default `main.py` remains untouched. No exchange order endpoints, credentials, trader, broker, or live-capital activation. This is NOT a real Binance feed test or 24h operating certificate.

## Cloud provenance

- Single-run code+audit verification: [GitHub Actions 39366721538](https://github.com/coffee2307/econith/actions/runs/39366721538), source commit `1ef5acbb5c62bfd28b1c15a1758ad0ab5474ecba`, SUCCESS, **4/4 PASS** (5.06 seconds).
- After extending independent SQLite observer audit: [GitHub Actions 39366997166](https://github.com/coffee2307/econith/actions/runs/39366997166), source commit `5b1ddd0a2013089a1a46a27946688770144bcf67`, SUCCESS, **4/4 PASS** (5.19 seconds). The same run demonstrates tampered primary receipt IDs and named subscriber projection digests are rejected.
- Repeated load/fault regression: [GitHub Actions 39367108929](https://github.com/coffee2307/econith/actions/runs/39367108929), source commit `c815ed97a8ebd2120c00db00f94d25ca71bb2402`, SUCCESS, **3 consecutive offline passes, 4/4 each = 12/12 PASS**, measured 5.17s / 5.05s / 5.03s.
- On that tested source commit, all three other specialized PR workflows (source-rotation, postseal HMAC and co-running HMAC) returned SUCCESS; whole-repository CI `39367112597` was still IN PROGRESS when this report was authored. Do not substitute this report for the final general CI conclusion.

## Engineering facts proven by offline tests

1. A single bounded, injected Spot source reader remains attached to one simulated WebSocket while original SQLite journals rotate. It retains the audited **64-frame upstream nonblocking FIFO**, **128-item capture writer FIFO**, and **5,000 ms local first receipt→original COMMIT limit** without silently relaxing freshness. The callback is fixture-only and is invoked after source journal close and fsynced local seal.
2. A separate background HMAC worker receives each just-sealed journal via a **maximum backlog of two segments**, offloading CPU/SQLite/child-process IPC away from the source event loop. The worker processes original→atomic capture intent→forward outbox→independent authenticated child receiver SQLite ACK→primary and two named derived observers→immutable per-segment checkpoint chain. A missing ACK, failed independent receiver or segment backlog pressure cannot produce a global PASS.
3. Per-segment turnaround is timed **from segment seal to completed ACK/derived/checkpoint**, with a default 20-second experimental cap. This is **not** a bound on every raw WS event→HMAC ACK, not public transport/network delay and not low-latency live trading.
4. A separate read-only observer independently recomputes all original/intent SHA-256 hashes, forward projection SHA fingerprints, receiver-owned receipt IDs and checkpoint SHA chains; it now ALSO opens three distinct derived-observer SQLite databases, verifies per-topic ordinal sequences, durable cursors, canonical JSON, projection SHA and receipt identities, and cross-matches named ledgers against the primary ledger. Tampering either primary or named receipt evidence is rejected.
5. The Linux process hard-exit gate injects `os._exit(63)` after a separate receiver subprocess has committed a receipt **but before producer ACK**, while a source reader is still active. It preserves global and per-segment orphan owner markers; the independent inspector cannot certify the partial run or silently take over.
6. Repeated 3x qualification exercises overlapping co-running reader/receiver, fail-closed injected receiver error, explicit opt-in refusal and the hard-exit boundary. Tests are offline fixture, not three independent real Binance sessions.

## Safety and explicitly missing institutional evidence

- The independently authenticated HMAC child runs after **each segment seal** while the next segment continues to capture, not per-frame real-time forwarding. The evidence only proves locally admitted-frame original completeness, not Binance venue-wide event history.
- The global source owner remains held while draining the bounded receiver queue; a graceful failure writes explicit quarantine, and a hard crash leaves orphan owner locks. No automatic orphan takeover, no distributed fencing or whole-device power-loss proof.
- The test does not certify a 24–72h persistent feed, sustained high-rate receiver load, exchange-to-kernel latency, memory/disk retention over long host uptimes, multiasset verified data coverage, or an audited `main.py` verified-only default cutover.
- It cannot retrospectively re-attest the ephemeral HMAC wire secrets from disk alone, even though authenticated subprocess IPC was exercised during the CI test.
- Retain gate scores: P1-04 **14/15**, P1-05 **13/15**, P1-06 **4/10**, P1-07 **0/10**, other gates **50/50**; Phase 1 **81/100 provisional, OPEN**.

## Next gate before 24h soak

Qualify an explicitly opted-in, tightly bounded **real public Binance Spot** source with a co-running but still non-executing HMAC receiver while preserving 64/128 queue limits. First measure source→original COMMIT latency and segment-seal→independent ACK age and quarantine intervals, review CPU/disk/network pressure, and repeat with forced WSS/IPC disconnects. Only after live bounded co-running results pass with private raw forensic retention should an owner-fenced persistent 24h–72h host trial be approved.
