# ECONITH Phase 1 — one-shot large cloud stress campaign with experimental reader fairness (2026-10-19)

**Research-only result: ALL THREE STAGES ACCEPTED BY THEIR OWN GATES** (no live orders). GitHub Actions: https://github.com/coffee2307/econith/actions/runs/39277291146 . Pinned, isolated branch `experiment/phase1-cloud-campaign-20261019`, workflow source commit `6379c9b4da47df982a5ecbe5b3c9412a4f24c315`. Finite-SIMULATION, no deployment/main merge, no production API keys. The original legacy default market path is unchanged.

## Why the frozen engineering score remained 81/100

See [PHASE1_SCORECARD_AND_CLOUD_CAMPAIGN_20261019.md](PHASE1_SCORECARD_AND_CLOUD_CAMPAIGN_20261019.md) and [PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md](PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md). This is seven distinct institutional acceptance criteria, not an increment-on-commit code completeness percentage. P1-04 14/15 still needs reviewed recovery; P1-05 13/15 still needs **genuinely continuous** durable verified ingress and long host soak; P1-06 4/10 still needs reviewed default verified-only runtime cutover; P1-07 0/10 still needs multi-symbol/OHLCV and independent 24–72h proof. A multi-session 600s PASS is an important engineering milestone, **not evidence of a 600s uninterrupted socket or missing history continuity**. Institutionally Phase 1 remains OPEN.

## Root cause isolation before the large run

A deterministic research fixture with 180 already-buffered WS messages and fixed FIFO 8 triggered terminal `QUEUE_BACKPRESSURE` in the original reader: `await _queue()` internally uses `put_nowait()` and does **not** yield control when successful. The full-cache opt-in `cooperative_reader_yield=True` adds `await asyncio.sleep(0)` only **after** a successful enqueue, giving the single durable writer a fair scheduling opportunity. This passed all 180 frames plus 1 independently persisted REST snapshot with synchronized book, matching original/capture intent rows and zero loss. An intentionally slow 150ms `on_persisted` verifier still correctly overflows, closes source, reports incomplete and quarantines. Thus fairness eliminates a **reproducible scheduler-starvation path** but does not bypass overload safety. The reader still stamps at await-return, keeps FIFO 128 for public data, uses full `WAL/synchronous=FULL` per original+intent COMMIT, independent HMAC receiver ACK and separate checkpoint/cursor, 5,000ms source admission-age gate and no order functionality. Defaults remain OFF; CLI and library demand all four reviewed experimental SQLite caches and separate public consent.

**Critical unproven aspect:** The underlying `websockets.connect(...,max_queue=16)` has its own upstream message buffer. Yielding after each receive may shift backlog upstream, where adapter receipt timestamps and its own FIFO high watermark cannot quantify total venue-to-process delay or verify a complete public feed. These results cannot close P1-05 without additional upstream-age/backpressure evidence.

## Stage 1 — Cloud Linux qualification SUCCESS

Ubuntu 24.04 standard cloud runner: **69/69** selected source/intent/receiver/owner-crash/forensic/reader-fairness tests PASS. Two sequential 240-frame, 5ms spacing full-cache offline trials with otherwise identical flags:
- Existing reader: `BOUNDED_OFFLINE_PASS`, original/cursor **241/241**, signed HMAC ACK/checkpoints 239/239, FIFO peak **2/128**, source→EventBus age max **133ms**.
- New cooperative reader: same **241/241** and **239/239**, FIFO peak **1/128**, max age **117ms**.
Both in SIMULATION, no real orders. These two different runner intervals do not establish a universal p-value/speedup.

## Stage 2 — Fresh three-session Binance finite matrix PASS 3/3

`ci.phase1_public_live_spool_matrix`, explicit four-cache+cooperative opt-in, separate HTTPS clock preflight+WSS+REST per segment; **141.52s total**, never overlapping sources, no retry.

| Attempt | Original/cursor | HMAC ACK/ckpt | FIFO peak /128 | pending original peak | Max admission age | p99 age upper | Freshness headroom |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1001/1001 | 970/970 | 1 | 114 | 837ms | 849ms | 4163ms |
| 2 | 1001/1001 | 993/993 | 1 | 173 | 1050ms | 1049ms | 3950ms |
| 3 | 1001/1001 | 991/991 | 1 | 116 | 943ms | 949ms | 4057ms |
| **Sum** | **3003/3003** | **2954/2954** | No overflow | Per-session only | All below 5000ms | Bound per-session | Worst 3950ms |

`ci.phase1_cloud_matrix_audit` independently read and `PRAGMA integrity_check`ed each disposable SQLite source/intent/receiver/checkpoint/cursor file: **3/3 `PASS_FINITE_ONLY`**; 0 mismatched source-intent SHA, 0 remaining owner fences, all closed and no incomplete source suffix. No external cryptographic re-verification of HMAC or exchange history completeness is claimed.

Aggregate-only artifact (3-day retention): https://github.com/coffee2307/econith/actions/runs/39277291146/artifacts/12198481310 . All 3003 enqueue, dequeue and writer-complete events matched with **0 FIFO overflow**, no 250ms bin truncation, peak FIFO 1; peak source SQLite COMMIT outlier ~207ms and independent HMAC ACK outlier ~190ms remained visible. Raw original SQLite and signing secrets were **not** uploaded.

## Stage 3 — Conditional 600s segmented watch PASS 11/11

Only launched after Stage 2 and independent read-only audit succeeded. **`watch_elapsed_s=605.499` (target 600–900s)**, 11 fresh isolated 1000-frame Binance sessions, every one `PASSED_FINITE_ONLY`. No auto-retries, no skipped or quarantined sources.

- 11×1001 = **11,011 original/intent/cursor** at full per-event COMMIT (all 11 source SQLite tables externally inspected on runner).
- **10,912 independently received HMAC ACK rows = 10,912 chained checkpoints**; zero source-intent hash mismatches and no owner fencing leftovers.
- All 11 saw peak adapter FIFO **1/128**, **zero overflow**; 250ms telemetry per session had 1001 enqueue, dequeue and writer-complete each, no truncated windows.
- Max exact observed source→EventBus age across the 11 attempts **669ms**; highest per-session p99 histogram upper 649ms; worst freshness headroom **4,331ms**; pending original peak **121**, below 512.
- **10 separate WebSocket session boundaries with UNKNOWN market history**; `history_continuity_verified=false`, `continuous_live_market=false`, no claim of 605s uninterrupted WSS; 0 real orders.
- Standalone read-only audit `ci.phase1_cloud_watch_audit`: **11/11 `PASSED_FINITE_SEGMENTED_ONLY`**, each original/intent/cursor exactly 1001, signed receiver count equals checkpoint count, source session closed, no owner fence or false historical coverage.
- Stage 3 artifact (3-day retention): https://github.com/coffee2307/econith/actions/runs/39277291146/artifacts/12199046863 . Only 5s+250ms aggregate diagnostics, per-stage histograms, limited operator verdicts and independent count audits. Raw SQLite is ephemeral; no off-runner institutional raw-evidence archive.

## Audit disposition and next gates

The new observed **11/11 PASS, 605.499s** is stronger than earlier negative tests: Windows 396.218s (4/5 pass, stale-projector failure) and previous cloud 427.066s (5/6 pass, FIFO overflow). **Preserve all three separately**: successful current finite watch does not retroactively turn either historic failure into PASS, and no probability of sustained correctness can be inferred from 11 finite sessions. Venue load, runner conditions, cache flags and source scheduling differ.

Next required work to legitimately increase frozen Phase 1 credit: (1) measure venue event-time lag against independently verified clock, transport/internal WebSocket queue pressure and upstream buffering; (2) prototype a single longer-lived verified continuously committed original source with bounded chunk rollover and crash/reconnect UNKNOWN history instead of repeatedly opening 1000-frame sessions; (3) privately retain and independently audit original SQLite WAL/FULL receipts for long run; (4) operator-approved P1-04 fenced recovery; (5) reviewed SIMULATION default runtime cutover P1-06; (6) multi-symbol and OHLCV validation with 24–72h operator-controlled tests P1-07.

**Final narrow conclusion:** This opt-in reader fairness fixed a deterministic event-loop scheduling starvation in an offline fixture and PASSED one cloud 3×1000 + 11×1000 read-only finite study. Production market-feed correctness, exchange sequence completeness, no-loss connection lifetime and institutional exit are NOT established. Draft PR #28 and real-capital lock remain unchanged.
