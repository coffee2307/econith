# ECONITH Phase 1 — Cross-process durable receipt and closed-session research handoff

**Branch:** Draft PR #28, no default startup wiring. **Scope:** research, offline
SIMULATION only; no execution, capital, exchange credentials, live-order route,
production deploy, or merge to `main`.

## Durable projection protocol: two independent SQLite databases

`infrastructure/market_cross_process_delivery.py` deliberately separates:

- **Producer DB:** `OfflineMarketProjectionCursor` commits an immutable
  `offline_market_projections` row *with* the producer cursor. Its existing
  `OfflineMarketProjectionInbox.claim_next(source_consumer, receiver)`
  issues a temporary exclusive lease for the earliest unacknowledged ID.
- **Receiver DB:** `OfflineIndependentProjectionReceiver` persists original
  canonical projection bytes and an idempotent
  `(source_consumer, receiver, ingest_id)` receipt in an independently opened
  SQLite WAL/FULL-synchronous file. Another OS process can own this database.
  The receiver **never** advances the producer's cursor and cannot skip
  a stored conflicting delivery under the same identity.
- **Sender ACK:** `OfflineCrossProcessProjectionSender.deliver_once`
  constructs a deterministic envelope from the *source-verified* projection
  claim, calls the trusted receiver endpoint/adapter, and only then calls
  `OfflineMarketProjectionInbox.acknowledge` with the receiver's durable
  receipt ID. This ACK is stored with the producer-side idempotency evidence
  in the producer DB; it is **not** an in-memory EventBus queue acknowledgement.

If sender dies after receiver commit and before producer ACK, the first source
claim expires. The next claim carries a **new** lease token for the same
unacknowledged `ingest_id`. The receiver sees identical bytes and returns
the **existing** receipt; it does not make a second write or invoke a strategy
callback. Sender can then durably ACK with the new claim. Disk-full faults on
either side leave observable durable state and do not silently skip data.
Reader-side receipt inspection validates both canonical bytes and receipt IDs.
Producer and receiver can be in independent OS processes; the regression
suite exercises a Python subprocess writing the receiver ledger.

### Deliberate limits

- **Not authenticated transport.** `receiver_accept` is an injected trusted
  callable/IPC adapter, not a production network RPC, TLS mutual-auth or
  broker protocol. The deterministic receipt digest is a checksum/identity,
  **not a keyed signature**. A malicious or lying callback can forge matching
  receipt fields; it is the integration's responsibility to authenticate the
  independently trusted receiver. There is no default remote transport.
- **Exactly-once scope:** exactly one row per durable receiver event ID in
  that receiver's **local SQLite effects**, with idempotent retry. Arbitrary
  network actions, trading, external databases and strategy side effects
  are **not** exactly once. Do not run external side-effectful callbacks
  inside this protocol.
- **Clock:** receiver `received_at_ms` must be >= source first
  `available_at_ms`, but caller-supplied fixture clocks and network clocks
  are not independently attested. Producer ACK requires a live, unexpired
  source claim and its own local processing clock.
- **Reliability:** the method intentionally has no internal background
  retry daemon. A supervisor must explicitly retry after source lease expiry.
  There is no deployed network service, operational alerting, DLQ or broker.
  Separate DBs do not share one atomic two-phase transaction; idempotent
  retransmission is the recovery mechanism.

## Original WS capture -> as-of integrity -> projection

`infrastructure/spot_verified_research_handoff.py` implements
`OfflineSpotVerifiedResearchHandoff(raw_capture_journal, integrity_journal)`.
Both are opt-in offline SQLite files. The method
`promote_session(session_id, as_of_ms=...)`:

1. Requires an already **closed** Spot capture session with retained original
   WebSocket/REST bytes. The as-of cutoff must not precede session close.
2. Replays and rechecks all original bytes and first-receipt provenance,
   validates contiguous aggregate-trade IDs, and requires a synchronized
   Spot depth book (REST snapshot bridged with `depthUpdate U/u`).
   Unknown/partial books **cannot** be represented as verified input.
3. Copies only original `aggTrade` payloads into a separate
   `OfflineMarketIntegrityJournal`, preserving their original
   `received_at_ms` as research availability rather than promotion time
   or Binance event time. The journal's own append sequence, gap and
   duplicate checks still apply.
4. Writes a durable `offline_spot_promotion_manifest` mapping source
   `(session_id, capture_id, original SHA-256)` to destination event ID
   and its source receipt. Reads revalidate both upstream raw capture and
   downstream market record. If process dies between source market commit
   and manifest insert, retry is safe because identical source event IDs
   deduplicate in the integrity journal.

The handoff cannot retroactively establish exchange completeness, re-fill
a reconnect outage, or prove TLS source origin. Source names are still
caller-asserted provenance. The default streamer uses partial `depth20`
snapshots and can apply SIMULATION overlays: it is **not** silently switched
to this verified path.

### Tests

- `tests/test_market_cross_process_delivery.py`: independent receiver
  process, separate database, duplicate replay, source ACK lost after
  receiver commit, receiver disk fault, sender ACK disk fault, conflicting
  payload, corrupted receipt, concurrent receiver claims, independent
  receiver IDs and REALITY-mode rejection.
- `tests/test_spot_verified_research_handoff.py`: complete three-database
  offline original-capture → integrity → projection → receiver-owned
  ACK chain, crash-window redelivery, closed-session requirement, malformed
  market continuity, insufficient book, original availability preservation,
  tamper-resistant lineage checks and replay-safe promotion after
  checkpoint failure.

## Phase 1 exit gates before actual Phase 2 promotion

| Gate | Status on Draft PR | Objective requirement |
|---|---|---|
| Original immutable Spot capture, scope, as-of and depth sync | Implemented offline | Independent replay on genuine time-stamped public WSS/REST captures |
| Bounded reconnect, outage ledger and generation-fenced controller ACK | Implemented offline | Clock uncertainty measurement; soak test across process death/network drift |
| Durable producer outbox + idempotent receiver-owned ACK in **separate** DB | Implemented offline | Authenticated transport/IPC and receiver crash/integration on actual workload |
| Default verified-only ingress and non-bypass evidence | **Not enabled** | Formal migration/cutover plan + end-to-end regression across all downstream subscribers |
| Production-grade monitoring/dead letter policy | **Not deployed** | Operator-approved alerts, bounded retries, and backlog runbook |
| Real-money execution | **Out of Phase 1** | Must remain disabled; separate explicit authority and safety gates |

**Acceptable engineering conclusion:** the offline core is substantially
implemented and tests can prove its local transactional properties.
**Unacceptable claim:** a green offline CI proves true Binance origin,
gap-free real observations, production-grade transport, trading profitability
or eligibility to enable REALITY mode.

The branch should remain Draft until non-fixture acceptance evidence and
integration sign-off. Code completion percentage must not be conflated with
operational verification percentage.
