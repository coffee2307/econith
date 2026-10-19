# ECONITH Phase 1 — cloud queue-backpressure microburst investigation, 2026-10-19

**Status:** Research-only instrumentation and deterministic offline tests PASS. The prior 10-minute GitHub-hosted multi-session watch **remains FAILED** after 427.066s (5 finite PASS, sixth quarantined with `SPOT_TRANSPORT_QUEUE_BACKPRESSURE`). The independent cloud finite 3×1,000 matrix earlier PASSED 3/3 and remains separate. Frozen provisional Phase 1 score remains **81/100, OPEN**, no orders. This report does not claim root cause closed.

## Forensic findings from the actual original GitHub artifact

Run https://github.com/coffee2307/econith/actions/runs/39254489969 and 3-day aggregate artifact https://github.com/coffee2307/econith/actions/runs/39254489969/artifacts/12186544460 were retrieved as a ZIP and each `attempt_001` through `attempt_006` original aggregate JSON was inspected. Sixth attempt's `spool_pressure.json` is `source_complete=false`, original/cursor 909/909; HMAC receiver ACK/checkpoints 900/900, none promotable to a finished 1,000-frame source.

At sixth attempt's **45–50s** window: 53 original commits / 53 durable cursors, peak ingress FIFO 1/128, pending originals peak 1, maximum receiver ACK ~3ms, event admission age 7ms.

At **50–55s**, 191 original commits / 191 durable cursors, but peak ingress FIFO **128/128** and pending original originals peak **167**. The per-enqueue monitor sampled at least **14 observations at >=90% FIFO capacity** before queue overflow. Maximum successful admission age 960ms; HMAC ACK max in that window 10ms. The source aborted soon after; the 5-second sampling interval **cannot resolve instantaneous arrival and dequeue rates** inside the window. Averages (38.2 original commits/s and 38.2 cursor completions/s) conceal transient imbalance.

Sixth attempt stage profile over all 909 accepted source records: `source_sqlite_commit` mean 0.532ms, p99 1.874ms, max 6.354ms; `receiver_hmac_ack` mean 0.998ms, p99 3.810ms, max 39.565ms; `eventbus_publish` mean 1.255ms, p99 3.979ms, with a single maximum 76.485ms. These stage maxima are not proven concurrent with the exact enqueue overflow. There is no evidence for a sustained 1-second HMAC wait or continuously slow raw SQLite commit in the failed five-second window. That is NOT evidence that host scheduling or occasional stalls were harmless; causality remains unproven.

The old Linux pressure diagnostic recorded `host_available_memory_bytes=null` in individual 5-second windows, so its per-window memory correlation is unavailable even though the runner's separate startup RAM preflight passed. The opt-in profiler now calls the reviewed Linux procfs/cgroup resource probe as well as Windows RAM probe.

## New bounded telemetry and recovery policy

- `BoundedSpoolPressure` now records **250ms fixed aggregate FIFO windows**, maximum **960** (four minutes maximum). Each includes successful enqueue, dequeue, writer completion, queue full/overflow counts, near-full observations, peak FIFO depth and maximum writer service duration. The new `subsecond_fifo_totals` remain bounded and event payloads, IDs, timestamps, addresses and account data are never copied. Existing five-second report/schema remains unchanged for compatibility. Truncation is explicit.
- `OptInSpotIngressAdapter` receives an **optional callback** for queue events only on `diagnose_pressure=True`. It does not change the actual `put_nowait` behavior, FIFO size 128 on public operator, source timestamps, source data, overflow refusal or SQLite transaction boundary. Overflow is still terminal and source remains incomplete.
- `ci.phase1_subsecond_forensics` reads only finite aggregate `spool_pressure.json` and extracts the six highest FIFO windows and 1-second aggregate history preceding the first measured overflow. This tool never certifies source HMAC cryptography or data continuity.
- Synthetic 250ms-window fixtures exercise overflow during a zero-gap burst and confirm retained SQLite original source session is closed; diagnostics remain `source_complete=false`. Invalid samples, diagnostic overflow of 960-window cap, and malformed source refuse acceptance.
- A separate bounded offline 240-frame fixture with 5ms spacing and four explicit SQLite cache flags PASSED: original/cursor 241/241; signed receiver ACK/checkpoints 239/239; FIFO enqueue/dequeue/writer_completed each 241; overflow 0; subsecond diagnostic about 5.7 KB, no raw frame copies. One 250ms window contained 115 enqueues and 115 dequeues while FIFO peak remained 1. Note that this is *synthetic* arrival scheduling and cannot replace Binance source burst evidence.

## Engineering decision and gates

**Do not increase FIFO, retry the 10-minute public soak, allow lossy drops, unbounded buffering or weaken WAL/synchronous=FULL.** The new evidence strongly suggests sub-five-second ingress batching / burst-rate mismatch **as a hypothesis**; to causally distinguish the exchange's actual frame rate from Python event-loop scheduling, preserve the 250ms sampled enqueue/dequeue and writer-service durations in the next **one** finite real Binance Public source (zero orders), with cloud RAM preflight and full opt-in profile. Compare output even if source fails, and retain verdict. Only consider a code-level throughput change after controlled offline A/B + hard exit tests. A single short PASS cannot close original Phase 1 P1-05.

Original/raw private-ledger retention is still unavailable on ephemeral public GitHub-hosted runners. Do not publish raw SQLite or HMAC secrets to public artifacts. P1-04 supervised recovery, P1-06 verified-only default cutover and P1-07 multi-symbol/OHLCV/24–72h remain OPEN.
