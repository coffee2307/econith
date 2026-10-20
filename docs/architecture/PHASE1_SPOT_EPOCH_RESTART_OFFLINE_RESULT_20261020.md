# ECONITH Phase 1 — Explicit segregated Spot epoch recovery: offline cloud evidence (2026-10-20)

**Scope:** SIMULATION-only offline fault injection with a fresh second injected socket and independent child HMAC reception. **No real Binance reconnect occurred in this test.** Frozen provisional Phase 1 score **81/100**, institutional acceptance OPEN.

## Pinned evidence

- Positive accepted test on pinned commit `a9ef40a3f9affe29cf12636024dfd9c3268f8869`: [GitHub Actions #39420789499](https://github.com/coffee2307/econith/actions/runs/39420789499), SUCCESS, **5/5 tests PASSED in 10.75 seconds**. The matching pull-request specialized gate [#39420795567](https://github.com/coffee2307/econith/actions/runs/39420795567) also SUCCESS.
- The earlier original implementation [#39420101100](https://github.com/coffee2307/econith/actions/runs/39420101100) also achieved **5/5 PASS**, but prior initial run [#39419946392](https://github.com/coffee2307/econith/actions/runs/39419946392) FAILED a report field mismatch; later PR run [#39420512984](https://github.com/coffee2307/econith/actions/runs/39420512984) FAILED under contended runner because the fresh fixture epoch timed out. Failures remain part of audit history. Increased only offline fixture window up to a bounded 3.8s and preserved source failure classes. No live source freshness or queue limits changed.
- Independent offline auditor `ci/phase1_spot_epoch_audit.py` opens source originals and downstream receipt/checkpoint SQLite directly and verifies actual file contents, including the original/intent SHA and source rolling chain of the quarantined prior epoch. It verifies the exact UNKNOWN boundary JSON and independently compares fresh-epoch receipt/checkpoint parity. Intentional tampering of `verified_gap_free_between_epochs` or accepted report `gap_free_between_epochs_certified` is rejected.

## What is qualified

1. First fixture source intentionally receives a Spot REST snapshot whose `lastUpdateId` cannot bridge the buffered order-book `U` in a subsequent sealed journal. A valid first sealed journal is independently rehashed. The whole epoch is **FAILED_OR_QUARANTINED**. The unsealed tail is never promoted to continuity.
2. An exclusive, fsynced `unknown_boundary.json` is written **before** the next source starts. It records a local last trusted receipt timestamp and expected unverified tail count. It does not claim exchange emission timestamps or exact unseen event counts.
3. A **fresh fixture lease** with separately scoped source, REST snapshot, original session IDs and capture journal starts. Previous order books and the old transport must not be reused.
4. New-epoch original/capture intents, authenticated independent HMAC receiver-owned ACK, three derived named/primary observers and hash-chained checkpoints pass existing separate read-only audit. Only the newly admitted scope may receive `PASSED_OFFLINE_NEW_SOURCE_EPOCH_ISOLATION_ONLY`; the *inter-epoch gap remains UNKNOWN forever*.
5. No consent, first epoch healthy, original prefix hash tampering, unauthorized public transport and falsified source-gap-free report markers are refused. No network account data, execution engines, positions, orders or default `main.py` cutover are involved.

## Unmet Phase 1 gates

**Real Binance public stream reconnect after live BOOK_NOT_SYNCED has not been demonstrated here.** Nor does this test certify the last raw unsealed frames, power failure/restart, 24–72h source and receiver liveness, exchange-wide history, protected archival of raw evidence or verified-only default runtime. P1-04 **14/15**, P1-05 **13/15**, P1-06 **4/10**, P1-07 **0/10**, other gates **50/50**, total **81/100** provisional unchanged.

**Next:** run an operator-approved bounded public Spot source epoch-reconnect fault campaign **with an explicit fresh socket**, an append-only UNKNOWN coverage record and independent sealed-source/HMAC audits after resumed capture. It must not relabel a broken epoch as continuous. Afterwards, qualify sustained reconnect/failure/replay, encrypted raw evidence retention and 24h host soak before any institutional acceptance.
