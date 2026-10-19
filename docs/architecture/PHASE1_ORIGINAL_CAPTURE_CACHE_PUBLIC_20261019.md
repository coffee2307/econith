# ECONITH Phase 1 — opt-in original-capture SQLite session cache, 2026-10-19

Status: FINITE RESEARCH EVIDENCE ONLY. Phase 1 remains OPEN at provisional engineering evidence credit 81/100. Earlier failed 10-minute watch (396.218s, 4/5 sources) remains FAILED. No live orders, production, default runtime switch or PR merge.

## Engineering change

Original capture plus immutable source intent are still written in a single SQLite BEGIN IMMEDIATE / COMMIT with WAL, synchronous=FULL, foreign_keys=ON and strict SHA256 provenance. Previously the source opened/closed SQLite on each write/read. New explicitly opted-in single-event-loop journal connection reuse avoids that overhead; unfinished transactions are refused and connection is closed at session cleanup. It does not introduce cross-DB atomicity or new recovery privilege. Public mode requires the reviewed bounded spool experiment and explicit derived, producer, receiver and original cache opt-in, retaining queue=128, max lag=5000ms, max pending originals=512, max frames=1000.

Safety tests: independent read-only SQLite verification, per-row source/intent count/hash equality, transaction abort triggered before source-intent insert (both records rolled back), actual child os._exit(67) after a committed source+intent with no connection cleanup (bytes and intent survive), and real hard-exit checkpoint/cursor crash test with all connection caches. A pre-existing receiver test startup race (DB file before schema initialization) was corrected by retrying only its precise no-such-table condition.

## Controlled offline A/B

240 synthetic WS frames at 20ms spacing, with all other derived/producer/receiver cache flags equal:

| Trial | Original connection reopened | Cached original connection |
| --- | ---: | ---: |
| Pair 1 wall time | 6.59s | 5.17s |
| Pair 2 wall time | 6.38s | 5.95s |
| Pair 1 original SQLite commit mean | 4.189ms | 0.767ms |
| Pair 2 original SQLite commit mean | 4.145ms | 0.781ms |
| Pair 1 original backlog max | 116 | 2 |
| Pair 2 original backlog max | 108 | 4 |
| Pair 1 source admission lag max | 1864ms | 197ms |
| Pair 2 source admission lag max | 1730ms | 243ms |

All four PASS 241 original/cursor and 239 independent ACK/checkpoint, zero orders. At 5ms synthetic spacing, both variants also PASS but original backlog still approaches ~200 (baseline max age 3169ms, cached 2322ms), so extreme bursts remain a service-rate risk.

## Binance Public finite pilots

Evidence remains only on authorized Windows host under F:\ECONITH-Phase1-Evidence (not stored in Git):

- 20261019_public_original_cache_160_v1: 161 original/cursor, 150 HMAC ACK/checkpoint, FIFO peak 14/128, backlog 14, max admission age 184ms, p99 upper 199ms, freshness headroom 4816ms, zero orders.
- 20261019_public_original_cache_1000_v1: 1001 original/cursor, 992 independent ACK/checkpoint, FIFO peak 48/128, backlog 78, max admission age 1053ms, p95 upper 549ms, p99 upper 949ms, headroom 3947ms, zero orders; process ~70.50s, finite PASS.

Independent read-only SQLite forensic inspection confirms original/cursor and receiver/checkpoint counts match, source closed, no owner fence, zero unprojected suffix, and source/diagnostic totals reconcile. It does NOT independently verify the HMAC secret or certify gap-free exchange history.

At public trial seconds 5–10, ingress original callback count was 181 while cursor completions were 107, queue peak 48/128 and pending originals 78. Maximum observed HMAC ACK in this sample was 17ms with zero >50ms samples, so this burst is dominated by mismatched input/service rates, not a demonstrated long receiver stall. Host available RAM changed but causation is unknown.

The previous public 1000-frame experiment without original+receiver caching observed queue 123/128, backlog 163, age 4586ms. Market conditions differ between samples; these numbers are NOT an apples-to-apples percentage uplift or sustained capacity guarantee.

## Decision and next gates

Keep all caches default OFF. Run reviewed, isolated 3x1000 real public matrix only after regression/crash tests and enough headroom, then explicitly audited timed observation. Failure history remains intact. Need long-duration continuous single-source feed, outage/reconnect gap reporting, operator-reconciled owner fence, verified-only default runtime, multi-symbol/OHLCV and 24–72h soak before institutional Phase 1 exit. Frozen provisional score 81/100 remains unchanged. Draft PR #28, SIMULATION, no orders.
