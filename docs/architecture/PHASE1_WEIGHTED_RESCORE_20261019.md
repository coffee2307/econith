# ECONITH Phase 1 — Re-score against the frozen institutional exit matrix (2026-10-19)

This is a **new evidence-credit assessment**, not an amendment to `PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md` or an institutional Phase 1 completion certificate. It uses exactly the seven original weighted rows, totalling 100; no row or denominator was moved to Phase 2. The pinned original baseline was **80/100 provisional**. The updated evidence-credit proposal is **81/100 provisional**, pending owner formal acceptance; every original mandatory institutional gate must still PASS before Phase 1 could be called 100%.

| Gate | Original frozen weight | Frozen baseline credit | Current proposed credit | New evidence and remaining obligation |
|---|---:|---:|---:|---|
| P1-01 source original, indexed immutable REST+diff, as-of replay | 20 | 20 | **20** | Bounded public BTCUSDT source verified; not global venue completeness |
| P1-02 availability, lineage, independent authenticated ACK | 15 | 15 | **15** | Independent per-envelope HMAC local subprocess receipt |
| P1-03 guarded raw/derived publishers and 3 durable observers | 15 | 15 | **15** | Source-bound SIMULATION-only EventBus/pipeline; legacy default remains separate |
| P1-04 crash/outbox/replay/fencing | 15 | 14 | **14** | Real OS hard-exit at checkpoint→cursor proven, owner marker preserved; audited operator-approved hard-kill recovery, true power failure and process takeover remain unproved |
| P1-05 sustained protected ingress, freshness, backlog, soak | 15 | 12 | **13** | Independent opt-in in-session public Binance Spot 3×1,000 matrix PASS **twice** with FULL SQLite per-event COMMIT, queue/backlog caps and p50/p95/p99 admission-age; still no verified continuous single feed or 24h hardware/network soak |
| P1-06 default app verified-only cutover and execution isolation | 10 | 4 | **4** | Opt-in research mode and AST guard only; **default remains legacy**, migration/review absent |
| P1-07 broader dataset symbols/OHLCV, 24–72h operator evidence | 10 | 0 | **0** | No verified broad dataset matrix, listing/delisting/vintage and current-HEAD 24–72h evidence |
| **SUM** | **100** | **80** | **81** | **INSTITUTIONAL PHASE 1: OPEN** |

**Why only +1:** P1-05 now has directly inspected finite real-source ingestion, two independent 3×1,000 acceptance runs, throughput improvement repeated offline, and fixed-memory bounded 50ms histogram p95/p99. The frozen 12/15 credit predates this stronger finite acceptance. Raising by **one** acknowledges engineering evidence without awarding the missing genuine sustained continuity and long-duration resilience. No additional point is due merely from the presence of an unproven test runner. The separate 10-minute *multi-session* watch being developed is NOT a gap-free feed: boundaries between any independently opened sockets are always UNKNOWN. A 10-minute pilot may justify future planning but does not by itself close P1-05 or P1-07.

Forensic evidence on authorized host remains under `F:\ECONITH-Phase1-Evidence\20261019_public_histogram_3x1000_v1`: 3,003 originals and 3,003 source-cursor positions, 2,974 HMAC receiver ACKs matching 2,974 chained checkpoints, all three accepted sources with p99 admission-age upper bounds 1,849ms / 3,299ms / 4,099ms and exact age maxima 2,047ms / 3,353ms / 4,197ms under fixed 5,000ms cap, **0 orders**. These figures are not a percentage of investment performance or an unconditional uptime/reliability estimate. No original evidence was changed or imported into Git.

Original matrix: [PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md](PHASE1_IMMUTABLE_EXIT_MATRIX_20261018.md). Supporting research report: [PHASE1_LAG_HISTOGRAM_HARDKILL_ACCEPTANCE_20261019.md](PHASE1_LAG_HISTOGRAM_HARDKILL_ACCEPTANCE_20261019.md).

**Governance:** PR #28 remains **DRAFT**, no main merge, no production deployment, no brokerage API keys, no REALITY, no executed orders or financial-capital authorization. This re-score is a documented conservative engineering opinion, not a newly frozen exit-matrix version.
