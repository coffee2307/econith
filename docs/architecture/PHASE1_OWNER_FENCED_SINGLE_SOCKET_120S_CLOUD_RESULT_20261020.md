# ECONITH Phase 1 — Owner-fenced public one-socket 120s cloud acceptance (2026-10-20)

**Outcome:** 2/2 ordered job PASS on research commit `1e9c7a7cf61576336582ee34ad411d3be9004b58`, and latest whole-repository PR CI is SUCCESS. This is source-only bounded research, not exchange completeness, persistent 24h service, HMAC downstream continuity or Phase 1 certificate. **Frozen Phase 1 evidence credit remains 81/100 provisional; institutional exit remains OPEN.**

## Immutable trial provenance

- Workflow: https://github.com/coffee2307/econith/actions/runs/39342455892, source SHA `1e9c7a7cf61576336582ee34ad411d3be9004b58`, success, run attempt 1.
- Ordered offline owner-fence/crash gate job ID `118367018992`: `10 passed` in 1.05s; intentional `os._exit(47)` test preserved orphan marker and refused second owner.
- Read-only real public Spot cloud trial job ID `118367097326`: success after offline gate. Independent read-only SQLite auditor output `PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY`.
- Whole repository PR CI on documentation HEAD: https://github.com/coffee2307/econith/actions/runs/39342465587 — SUCCESS.
- Aggregate-only artifact: `econith-phase1-owner-socket-39342455892`, artifact ID `12229751645`; GitHub ZIP digest `sha256:83fe01660c711725c6e90570a29ad6ba8b58a58fef7536bfe373ae799854c428`; expires 2026-10-22T23:37:26Z, size 1181 B. Raw public source/SQLite **not** uploaded to public artifacts.

## Observed single-socket data

| Metric | Exact cloud result |
|---|---:|
| Same TLS WebSocket observed duration | **120.002s** |
| WebSocket frames | **1,869** |
| Read-only REST snapshots | **1** |
| Original SQLite rows | **1,870** |
| Same-COMMIT source capture intent rows | **1,870** |
| Original/SHA mismatch | **0** |
| Independent SQLite source sessions | **1** |
| Depth book state | **SYNCED** |
| FIFO peak vs cap | **2/128** |
| Last-source-frame silence at deadline | **0.028s** (<= 5s threshold) |
| Exclusive owner held for source run | **true** |
| Orphan lock after graceful PASS | **false** |
| Public clock RTT | **510ms** |
| Approximate public clock offset | **+199ms** |
| Real orders | **0** |

**Important scope flags:** `history_continuity_verified=false`, `exchange_history_completeness_certified=false`, `source_event_losslessness_certified=false`, `hmac_forward_receiver_tested=false`, `default_runtime_cutover=false`, `institutional_phase1_complete=false`. Local producer original SQLite integrity verification is not an independently authenticated venue feed. Local source COMMIT freshness and terminal read liveness are not direct end-to-end exchange-to-consumer latency measurements.

## Remaining institutional gates and work

- P1-04: 14/15. Process hard-exit orphan refusal exercised, but no operator-reviewed external power-failure takeover and correct reconstructed restart audited.
- P1-05: 13/15. Source-only bounded one-socket observation improved to 120s, but no persistent 24–72h source, rotating durable journal with coordinated downstream cursors, verified catch-up gaps or source-to-receiver/derived HMAC continuity.
- P1-06: 4/10. Default `main.py` still legacy; needs independent safe migration and execution isolation review.
- P1-07: 0/10. Earlier finite 600 OHLCV observations are separate proof; listing/delisting and 24–72h data-quality vintage/retention remain unaccepted.
- Previous failed source/fixture tests retained, no CI rewrite or retroactive verdict changes. Draft PRs #28–#31 and `main` remain unmerged.

**Next engineering gate:** design a finite-*segment rotation* with a single continuously read TLS socket, fail-closed rollover barrier and durable source offset bridge; add per-segment original-to-independent-receiver HMAC and checkpoint proof, then deliberate mid-rotation hard kill; keep UNKNOWN intervals explicit if a source disconnect or operator stops the stream. Do not schedule 24h soak until this path has safe bounded resource retention and recovery evidence.
