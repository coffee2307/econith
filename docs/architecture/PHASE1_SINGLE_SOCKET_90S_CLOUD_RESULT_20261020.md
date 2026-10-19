# ECONITH Phase 1 — Verified 90s ONE-source public Spot socket pilot (2026-10-20)

**Disposition:** 2/2 ordered jobs **SUCCESS** on one immutable code commit. Bounded 90.001s attached read to one public Binance Spot WebSocket is a positive P1-05 engineering proof. It is NOT a 24h continuous service, signed venue completeness, HMAC downstream admission, approval for default verified-only cutover, or Phase 1 institutional completion. **Retain provisional 81/100** pending frozen gates.

## Provenance

- Workflow run: https://github.com/coffee2307/econith/actions/runs/39340787906
- Pinned tested commit: `5b6c0a3c37216de509eec8bda89a8de17a1b0c8e`
- Branch: `experiment/phase1-single-socket-duration-20261020`
- Ordered offline job `118361525219`: **success**, 7/7 deterministic pytest tests
- Public single-socket job `118361595708`: **success**, no automatic retry of failed samples
- Artifact: `econith-phase1-one-socket-39340787906` ID `12229385554`; SHA-256 of GitHub artifact ZIP `0b65ae18d464e19cd84aec91471a6ba4302953f2ecfa1ac1a9c10964bf8ac547` (GitHub reports expiry `2026-10-22T23:20:54Z`). Artifact contains only aggregate report and independent local audit, **NOT source-original SQLite/raw frames**.

## Observed facts from job log

| Indicator | Evidence |
|---|---:|
| Single transport stream observation duration | **90.001s** |
| Source WebSocket frames captured | **1,227** |
| REST depth snapshots captured | **1** |
| Original SQLite rows | **1,228** |
| Same-COMMIT capture intent rows | **1,228** |
| Original bytes vs intent SHA mismatches | **0** |
| SQLite independently read-only audited source sessions | **1** |
| FIFO high-watermark | **1/128** |
| Depth after independent original replay | **SYNCED** |
| Local public clock RTT / offset estimate | **586ms / +237ms** |
| Real order requests | **0** |

Offline gate was initially **FAILED** at run `39340595707` because the synthetic fixture incorrectly presented a REST snapshot older than the earliest accepted depth interval, which properly led to `BOOK_NOT_SYNCED` quarantine. Run `39340691067` exposed the sanitized error and likewise failed. The fixture was corrected to form an actual snapshot/diff bridge (first buffered diff covers its snapshot update id). The later pinned run passed both jobs. Do not erase or relabel those earlier failures.

## Security and non-claims

- Uses opt-in public TLS GET and WebSocket only, in SIMULATION. No credentials, CCXT connection, order submission, mutable API routes or default app ingress changes.
- Frame-quota exhaustion before 90s, premature EOF, transport idle, SQLite failure, malformed event, stale receipt, failed initial REST synchronization or FIFO 128 overflow must fail-closed.
- 5,000ms source-event and committed-original admission-age caps were configured and checked. The outcome doesn't reconstruct exchange-to-OS kernel/network-library buffering latency.
- One observed non-disconnected socket does NOT verify all actual venue trades, downstream capture-to-HMAC publication, 24–72h resource stability, OS crash-restart fencing, full historical Spot completeness, or inter-run history. `exchange_history_completeness_certified=false`; `hmac_forward_receiver_tested=false`.
- Raw original evidence was intentionally not retained outside the ephemeral cloud runner. The SHA digest only attests the **aggregate artifact ZIP**, not an externally reproducible original historical ledger. Controlled private raw retention is still required.

**Frozen exit score:** P1-04 14/15, P1-05 13/15, P1-06 4/10, P1-07 0/10, other gates 50/50; **81/100 provisional**. Full Phase 1 OPEN, no production/merge/live trading authority.

## Next implementation prerequisites

1. Design an owner-fenced persistent single-reader capture service that supports journal rotation and continuous downstream HMAC admission without 4,998 record cap; preserve immutable source IDs and explicit UNKNOWN reconnect gaps.
2. Test forced WSS disconnect, stale snapshot, writer stalls, worker hard-exit and device power interruption with full private raw replay, two workers and lease takeover denial.
3. Before any 24h/72h operator evidence request, implement bounded disk/CPU/memory checks, scheduled integrity sampling, auto-quarantine, restart intervention and immutable receipts. Do not declare a timed pilot institutional 100%.
