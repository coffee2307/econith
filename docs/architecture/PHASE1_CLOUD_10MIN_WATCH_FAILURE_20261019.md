# ECONITH Phase 1 — cloud 10-minute multi-session watch FAILED on transport queue backpressure

**Date:** 2026-10-19. **Review only; no live trades.** Workflow https://github.com/coffee2307/econith/actions/runs/39254489969. Branch of execution: `experiment/phase1-cloud-watch-20261019`, commit `6f511dd0afa6dc7c4ce6cb89670b80ea82627ede`. **Institutional Phase 1 OPEN, provisional score remains 81/100**. This report preserves a *negative* test result without deleting or reclassifying existing proofs.

## Exact outcome

The Ubuntu 24.04 runner passed its 20 focused unit/crash-safety tests and resource preflight (15.69GB physical RAM available, 91.21GB free disk). The bounded public Binance segmented research soak then returned **`FAILED_OR_QUARANTINED`**, process exit code 2, **427.066 elapsed seconds** of the required 600–900 seconds. The first five independent 1,000-frame sources each PASSED; the sixth source was quarantined on **`SPOT_TRANSPORT_QUEUE_BACKPRESSURE`** after **57.719s**. The sixth source did not satisfy the required finite 1,000-frame target. No automated retry occurred.

| Source | Status | Runtime | Original committed/cursor | Independent HMAC ACK/checkpoint | Peak admission age | Histogram p99 upper |
|---|---|---:|---:|---:|---:|---:|
| 1 | PASSED_FINITE_ONLY | 81.650s | 1001/1001 | 987/987 | 186ms | 149ms |
| 2 | PASSED_FINITE_ONLY | 59.142s | 1001/1001 | 993/993 | 572ms | 549ms |
| 3 | PASSED_FINITE_ONLY | 80.604s | 1001/1001 | 988/988 | 301ms | 299ms |
| 4 | PASSED_FINITE_ONLY | 84.129s | 1001/1001 | 994/994 | 208ms | 149ms |
| 5 | PASSED_FINITE_ONLY | 63.804s | 1001/1001 | 991/991 | 275ms | 249ms |
| 6 | FAILED_OR_QUARANTINED | 57.719s | **909/909 incomplete** | **900/900 incomplete** | Not accepted | Not accepted |
| **Total** | **5 PASS / 1 FAIL** | **427.066s** | 5914/5914 across all six observed source ledgers | 5853/5853 across six | No continuous statistic | No continuous statistic |

**Important:** 5,005 original/cursor positions and 4,953 HMAC ACK/checkpoints belong to the five completed sources. The sixth's additional 909 original/cursor and 900 ACK/checkpoints are **forensic counts only**; they do not make that session complete. Five market-history boundaries are **UNKNOWN**, not certified continuous. The independent read-only SQLite inspector reported five matched, closed sources and one incomplete source; capture-intent hash mismatches were zero for each inspected source. Its `PASS` flags are bounded count/integrity checks, **not** independent cryptographic revalidation of HMAC signatures, Binance completeness, or power-loss certification. No orders submitted.

## Evidence and limitations

Public aggregate-only GitHub artifact (3-day retention): https://github.com/coffee2307/econith/actions/runs/39254489969/artifacts/12186544460. Original SQLite and raw Binance data from ephemeral cloud runner were intentionally NOT published on the public repository or uploaded as artifacts, so post-run raw replay is unavailable. The artifact contains short aggregate per-window FIFO pressure, HMAC receiver ACK diagnostic, and stage latency summaries for six sessions; those should be reviewed before any change to the ingestion pipeline.

The failure code **`SPOT_TRANSPORT_QUEUE_BACKPRESSURE`** means the bounded transport FIFO was unable to accept a source frame. Unlike the historical Windows watch failure `FORWARD_PROJECTOR_SOURCE_LAG`, this is an *ingress overflow* safety gate. **Do not infer the root cause from the code alone**: upstream exchange burst volume, original SQLite commit time, downstream backpressure, process scheduling and network read cadence can all contribute. The Ubuntu host had passed RAM preflight, but there is no proof that physical RAM caused or did not cause this specific FIFO saturation. A source-side five-second FIFO-depth and producer/consumer service-rate timeline is the next forensic task.

## Required next sequence

1. Review the six aggregate `spool_pressure.json` and `stage_latency.json` windows, particularly 5 seconds preceding failure; compare ingress frame count, FIFO near-full observations, original journal commit timing, per-stage HMAC IPC and backlog slope. Do not reset, enlarge or silently drain the FIFO to obtain a PASS.
2. Add precise lossless queue-enqueue/dequeue observability and fault-injection under deterministic burst if current aggregate counters cannot explain the overload; cap CPU/memory and keep all per-event FULL commits.
3. Validate revised hypothesis with offline A/B and child-process hard-exit/capture-intent atomicity tests. Only then run a fresh, independently versioned **short public** trial, followed by a new 10-minute observation if margins justify it. Never retry automatically or hide prior failed attempts.
4. For institutional evidence, create private access-controlled retention for original SQLite receipts and independent source replay/verification; public aggregate-only artifact is not adequate to claim durable archival history.
5. Separate frozen gates remain OPEN: operator-approved fenced recovery (P1-04), sustained uninterrupted source ingress/24h proof (P1-05), verified-only runtime default cutover (P1-06), multi-asset/OHLCV and 24–72h reviewed evidence (P1-07).

No real execution, broker credentials, production deployment, or main branch merge. The old Windows aborted 396.218-second watch and the new cloud 427.066-second aborted watch remain separate, honest negative evidence. The finite 3×1,000 cloud PASS remains separate, honest positive evidence.
