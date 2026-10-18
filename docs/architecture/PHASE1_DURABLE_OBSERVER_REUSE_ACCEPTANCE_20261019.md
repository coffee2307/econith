# ECONITH Phase 1 — durable observer SQLite connection reuse: controlled A/B and real public matrix (2026-10-19)

**Decision:** experimental finite, research-only **3×1,000 public Spot in-session spool PASS**, with independent SQLite verification. **Not institutional Phase 1 exit; not an Hour/Day result; not a production/runtime cutover; not proof of uninterrupted venue market history.** Draft PR #28 remains OPEN.

## What changed (small and safety-preserving)

The per-record verified research pipeline previously opened and closed SQLite connections for **three separately owned durable derived research ledgers**: `derived.sqlite`, `research_audit.sqlite` and `research_monitor.sqlite`. Each durable observer still performs an independent `BEGIN IMMEDIATE → per-topic ordinal/identity validation → row/cursor update → COMMIT`, with WAL/`synchronous=FULL` and no producer/receiver/observer shared transaction. Opt-in `reuse_connection=True` retains the *same respective per-ledger SQLite connection* across the finite trusted research session. No write batching, asynchronous fire-and-forget, changed durability PRAGMA, increased FIFO capacity, extended freshness budget or skipped independent HMAC receiver/derived/checkpoint is involved. On exit, the EventBus stops and the individual ledger connections close.

Default use stays `reuse_connection=False`. Public A/B execution requires an explicit manually opted-in **experimental** in-session research trial, with clock preflight, 128 ingress FIFO, independent persistent authenticated receiver, max 1,000 WS frames per source, 120-second source timeout, max 512 unprojected originals, max 200 MB raw source bytes and max **5,000ms source-to-publication age**. The new public CLI `--reuse-derived-connections` does NOT affect production, default startup or the pre-existing operator `Load` profile.

## Synthetic A/B, same deterministic-structure stream

Three independent paired trials each supplied 240 synthetic WS frames at 20ms per frame on the same Windows host. Previous per-event connection-opening baseline **quarantined 3/3** with `FORWARD_PROJECTOR_SOURCE_LAG`; opt-in retained-connection pipeline **completed 3/3** with 241 committed originals including REST fixture snapshot and 239 authenticated receiver ACKs per success. Retained-connection totals **10.59s, 10.12s and 10.55s**; maximum source→publication ages **4,213ms, 4,144ms and 4,481ms**. EventBus publisher stage means from the *different-length* diagnostic samples were ~19.4–19.9ms (failed baseline) versus ~5.3–5.9ms (retained connections), which is a promising mechanism indication **not a controlled percentage uplift** because the failed attempts process fewer frames.

Tests specifically verify each per-event COMMIT becomes immediately visible to a separate read-only SQLite connection, an injected exception before COMMIT rolls back both receipt and cursor, conflicting same-ordinal replay fails closed, and `os._exit(49)` directly after commit preserves the independent receipt after re-open. Targeted local regression before public trial: **48 PASS**. GitHub Python CI, Docker images and container smoke PASS on reviewed code commit `143d4dc54b0dd618a8b81f189610c1f4d7a3bcce`.

## Real public Binance Spot, fully isolated new source sessions

Fresh evidence root on the authorized Windows host (kept OUTSIDE Git):
`F:\ECONITH-Phase1-Evidence\20261019_public_cached_observers_3x1000_v1`

Manual 160-frame pilot: **PASS**, original 161, ACK/checkpoint 148, cursor 161, FIFO peak 13/128, projector pending 17, max freshness age 626ms.

Independent operator matrix: `python -m ci.phase1_public_live_spool_matrix --allow-public-market-data --accept-experimental-spool --reuse-derived-connections --output-dir <fresh-absolute-evidence-root> --frames 1000 --max-lag-ms 5000`. Each one uses own public clock preflight and fresh public TLS WSS+REST, own source original+atomic intents, receiver child/HMAC ACK ledger, producer outbox, primary and two named derived observers, checkpoint hash chain and independent original cursor.

| Real attempt | Source originals including fresh REST snapshot | Durable cursor | Authenticated HMAC receiver ACK | Signed checkpoint rows | Ingress FIFO peak | Largest pending original backlog | Source→projector age max | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 1,001 | 1,001 | 993 | 993 | 23/128 | 37 | 1,425ms | PASS |
| 2 | 1,001 | 1,001 | 991 | 991 | 38/128 | 80 | 3,196ms | PASS |
| 3 | 1,001 | 1,001 | 991 | 991 | 36/128 | 125 | **4,790ms** | PASS |
| TOTAL | **3,003** | **3,003** | **2,975** | **2,975** | No overflow | See individual | Below 5,000ms | **3/3 PASS** |

The matrix exited **0** in 251.562s (tool wall runtime 252.31s), official exact verdict `PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY`, **zero exchange orders**. Separate read-only SQLite inspection verified original, cursor, receiver and checkpoint counts and no remaining owner lock for each accepted attempt. 28 original records across three attempts do not map 1:1 to forward ACKs because pre-sync/ineligible originals are not market projections; the complete source-inventory, projection eligibility and receipt reconciliation runs in each sealed candidate. The original reference matrix before this optimization was **1/3 PASS**: two runs quarantined from `FORWARD_PROJECTOR_SOURCE_LAG`.

## What this does and does not prove

**Proved, limited:** opt-in finite public in-session original capture and research delivery may stay within the 5s lag bound during three independently clock-checked 1,000-WS-frame sessions without FIFO overflow or missing accepted projection ACK/checkpoint. There is repeated synthetic A/B evidence of reduced observer overhead. The raw ledgers remain full durability, HMAC-authenticated receiver is separately owned, and record/source as-of checks remain mandatory.

**Still unknown/not accepted:** it is **not** a stochastic long-duration or worst-case burst performance guarantee. Attempt #3 reached **4.79s / 5.00s**, leaving only 210ms freshness headroom. Do not change the 5s limit merely to pass. Need longer sustained soak and fault-injection for SIGKILL + reopening with cached observer connection, physically disconnected WSS, SQLite disk full/lock/paging, process fencing, no history gap inference and consumer reconcilation. Default verified-only runtime cutover (P1-06), broader OHLCV/multi-asset (P1-07), and 24–72h operator acceptance remain OPEN. Prior frozen provisional **80/100** engineering score stays unchanged until review against the official exit matrix; no claim of institutional P1-05 gate completion.

**Next engineering increment:** measure time-series p99 original→publish age during uninterrupted timed observation, optimize remaining outbox/checkpoint/IPC only with controlled A/B and crash proof, add operator report for detailed lag headroom, then stage progressive timed soak with honest outage windows. Maintain SIMULATION-only, PR #28 DRAFT, no main merge, production, broker, or real funds.
