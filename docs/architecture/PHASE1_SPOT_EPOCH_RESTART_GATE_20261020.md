# ECONITH Phase 1 — Explicit source epoch isolation after Spot BOOK_NOT_SYNCED (2026-10-20)

**DRAFT offline engineering gate.** This is NOT an automatic public Binance reconnect service, not a data-completeness proof and not approval for live trading.

## Why this exists

A real public [GitHub Actions run #39406576289](https://github.com/coffee2307/econith/actions/runs/39406576289) correctly quarantined after a depth snapshot/diff synchronization failure (`BOOK_NOT_SYNCED`), with 1,800 locally committed WebSocket frames across four sealed journals and an unverified tail. The earlier successful 120s source/HMAC trials must not erase that negative evidence. The missing part of P1-05 is proving a **fresh source epoch** is isolated from the broken one, not converting a failure into a gap-free PASS.

## Offline test contract

1. An operator explicitly authorizes a *fixture-only* finite test. The initial source epoch captures and seals a valid original journal, then receives a deliberately unbridgeable REST snapshot for the next journal. That second journal and its original frames remain **unverified tail**, while the whole first epoch is `FAILED_OR_QUARANTINED`.
2. Read-only independent prefix inspection opens the previous epoch's sealed original and same-COMMIT capture intent SQLite records. It recomputes every original SHA-256 and the chained source hash, checks closed session/one REST snapshot/depth SYNCED, and certifies only the closed prefix, never the failed source aggregate as a whole.
3. Before starting any new socket, an fsynced `unknown_boundary.json` marks `UNKNOWN_COVERAGE_PENDING_FRESH_EPOCH`, stores the last trusted *local* receipt boundary and number of unverified produced frames, and makes `gap_free_between_epochs_certified=false`. This is not an exchange emission-time interval or signed source gap.
4. A **different fixture factory** creates a separate source lease and a fresh set of `rec_rot_` session identities under a distinct `epoch_0001` root. It must acquire a new REST snapshot for every new sealed journal. The co-running research HMAC child delivers eligible projections to independent receiver receipts, three named observer ledgers and per-source checkpoint chain.
5. Only if the new source and independent original→intent→receiver ACK→checkpoint audit both pass does the coordinator emit `PASSED_OFFLINE_NEW_SOURCE_EPOCH_ISOLATION_ONLY`. It **never** emits a cross-epoch continuity certificate, certifies a market history gap, retries the old session, reuses an old order book, or resumes live trading.
6. No operator consent, insufficient sealed original evidence, source prefix tampering, healthy original epoch, invalid new source journal or missing receiver ACK must refuse and preserve a failure/quarantine marker. Unresolved old-owner locks require manual review.

## Institutional status

This proves the model for **isolated supervised recovery with permanently UNKNOWN coverage**, not a real public WSS reconnect or sustained 24–72h service. P1-04 power-loss recovery, P1-05 live fault/reconnection, P1-06 audited verified-only default runtime and P1-07 multiasset source quality remain OPEN. Engineering score **81/100 provisional unchanged**, no merge to `main`, no account/API keys or orders.
