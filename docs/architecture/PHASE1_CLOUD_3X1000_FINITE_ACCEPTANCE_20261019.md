# ECONITH Phase 1 — first GitHub-hosted cloud 3×1,000 Binance Public matrix, 2026-10-19

**Verdict: PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY (3/3).** This is a *new*, fully opt-in cloud research test; it does not supersede the previously FAILED 10-minute Windows watch (396.218 seconds), or close institutional Phase 1. Provisional frozen engineering evidence credit remains 81/100. Zero trades, no credentials, SIMULATION.

## Cloud migration and environment
After Windows RAM fell below the new research preflight floor (approximately 0.89–1.18GB free), a disposable GitHub Actions ubuntu-24.04 runner was used instead of leasing a GPU-backed RunPod pod. ECONITH is a public GitHub repository; standard public-repo GitHub-hosted runner is documented with 4 vCPUs and 16GB RAM. Run 39252752983 reported 15.68GB physical RAM available, 91.21GB disk free at preflight, and zero cloud provider keys.
One-off branch: experiment/phase1-cloud-public-20261019. The workflow was triggered only by file modifications on that branch; never by main, PR, or schedule. An existing incomplete repo submodule configuration (econith_social lacks a .gitmodules URL) caused the first standard checkout attempt to fail before any Binance call. The workflow was corrected to download an immutable GitHub commit tarball, without modifying submodule metadata. No source test was counted for those two initial infrastructure failures.

## Valid matrix
Run: https://github.com/coffee2307/econith/actions/runs/39252752983 (SUCCESS). All 20 cloud preflight/SQLite/recovery unit tests PASS. Research duration 212.807s. Fresh public Binance Spot REST+WSS source and clock preflight for EACH attempt. 128 FIFO, 5,000ms max source freshness, at most 1,000 public WS frames per attempt, separate original/capture-intent/outbox, HMAC receiver child, derived research observers, checkpoint and cursor; WAL/synchronous FULL per-event COMMIT. Derived/producer/receiver/original caches all explicitly enabled only for this finite test.

| Attempt | Original/cursor | HMAC ACK/checkpoint | Queue peak /128 | Pending original peak | p95 age upper | p99 age upper | Max exact source age | Freshness headroom |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 1,001/1,001 | 991/991 | 11 | 41 | 199ms | 349ms | 395ms | 4,605ms |
| 2 | 1,001/1,001 | 987/987 | 8 | 20 | 99ms | 149ms | 250ms | 4,750ms |
| 3 | 1,001/1,001 | 976/976 | 25 | 65 | 399ms | 549ms | 581ms | 4,419ms |
| **Sum** | **3,003/3,003** | **2,954/2,954** | No overflow | Per segment | No aggregate pooled p95 | No aggregate pooled p99 | Below 5,000ms | Worst 4,419ms |

Independent on-runner *read-only* forensic verification PASS 3/3: each original ledger has 1,001 matching capture-intents, 0 missing/incorrect SHA hashes, cursor 1,001, receiver rows equal chained checkpoints, no leftover producer owner fence, closed source session, and no unprojected original suffix. Checks include PRAGMA integrity_check. This audit does NOT independently verify HMAC cryptographic signatures, true Binance trade-history completeness, unbroken WebSocket continuity, or any source-gap-free period. The code under test does enforce authenticated receiver delivery before commit, but the after-run inspector is deliberately more limited.

## Evidence retention/privacy
Public repository GitHub artifact: https://github.com/coffee2307/econith/actions/runs/39252752983/artifacts/12187180009 . Retention 3 days; contains only aggregate matrix report, cloud_audit.json, and age/pressure/stage JSON summaries (11 files, 9,616-byte compressed archive). NO original SQLite/market payloads, HMAC key material, account secrets, or private Windows files were uploaded. Raw original ledgers lived only on the ephemeral cloud runner and are not reproducible later from artifact alone. This is a material limitation for an institutional source-evidence chain; longer soak should target a protected object store with a dedicated retention policy before any institutional completion claim.

## Decision and next
Continue the dedicated *finite multi-session* 10-minute observation in the cloud only after adapting the timed watch to Linux RAM/procfs/cgroup and explicitly reviewing all four cache flags. It must log UNKNOWN history between each new WebSocket and fail on stale/queue/resource violations; it is NOT a 10-minute single uninterrupted connection. The previous Windows failed soak remains FAILED. Genuine single-connection continuous feed, supervisor reconnect, protected raw evidence retention, reviewer-authorized orphan recovery, verified-only default runtime cutover, multi-symbol/OHLCV and 24–72h are still OPEN. Do not merge PR #28, deploy or place real orders.