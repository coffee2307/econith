# ECONITH Phase 1 — full SQLite-cache 3×1000 public operator gate (2026-10-19)

Status: IMPLEMENTED AND TESTED, but NOT EXECUTED against public Binance as a new three-session matrix. Earlier single 1000-frame full-cache run PASS does not mean 3×1000 PASS. Phase 1 OPEN, provisional 81/100, earlier 10-minute watch FAILED at 396.218s, PR #28 DRAFT. SIMULATION only, no orders.

## Implementation
The ci.phase1_public_live_spool_matrix operator now accepts separate explicit derived, producer, receiver and original SQLite cache flags plus bounded diagnostics. Original cache requires receiver, producer and derived cache; each session still checks the public HTTPS clock, opens distinct Binance WSS+REST, owns separate original+intent/outbox/HMAC receiver/derived/checkpoint/cursor ledgers, uses WAL/FULL per-event COMMIT, and enforces 128 FIFO, at most 1000 frames, a 5000ms freshness deadline and no live trading.

The manual operator checks available Windows physical RAM >=1,500,000,000 bytes, disk free >=2GB and in-root evidence <=500MB before and after EACH source. Below the gate, no further public session starts. NOT_ATTEMPTED_HOST_RESOURCE_GUARD is distinct from FAILED_POST_SESSION_RESOURCE_GUARD. The operator report records actual sources started and resource-skipped attempts, and its IN_PROGRESS_NOT_CERTIFIED progress is fsync-written then atomically replaced after each completed attempt. No source/ACK ledger is overwritten and skipped attempts cannot count as PASS.

## Host blocker (2026-10-19)
Read-only Windows preflight found initially 1.18GB free physical RAM and 76.3GB free F: disk. After targeted tests, available RAM fell further to 0.89GB; the operator's shared computer has approximately 8GB RAM with Chrome/ChatGPT and PHAM/Beatriz workloads. The 1.50GB headroom requirement was NOT MET; the public 3×1000 test was NOT launched. Do not kill unrelated applications or infer low RAM definitely caused prior freshness incidents.

## Tests and continuation
Offline operator tests cover flags and reject invalid cache combinations, pre/post-resource refusal, accepted finite 3-trial results, preservation of quarantined middle trial, and checkpointed partial operator report. Windows targeted tests PASS (16/16 on final local targeted run), and CI status must be checked at final HEAD.

When host resources are adequate, recheck RAM/disk and GitHub CI before any source trial, then run one explicitly authorized full-cache 3×1000 matrix using the new four cache flags and --diagnose-pressure; verify source/intent/cursor, independent HMAC ACK and checkpoints read-only for every completed session. Compare queue, p95/p99, max lag, backlog, ACK stalls, and RAM. All inter-session market-history intervals remain UNKNOWN. The separate 10-minute timed soak is still not passed, and P1-04 operator-reviewed recovery, P1-06 verified-only default runtime, P1-07 multiple symbols/OHLCV/24–72h remain open.

Intended manually invoked command (NOT run during the low-RAM gate):

cd F:\econith
python -m ci.phase1_public_live_spool_matrix --allow-public-market-data --accept-experimental-spool --reuse-derived-connections --reuse-producer-connections --reuse-receiver-connection --reuse-original-connection --diagnose-pressure --output-dir F:\ECONITH-Phase1-Evidence\20261019_public_fullcache_3x1000_v1 --frames 1000 --max-lag-ms 5000

Operator must ensure that output root is fresh and never previously used. Default market sources and production remain unmodified.