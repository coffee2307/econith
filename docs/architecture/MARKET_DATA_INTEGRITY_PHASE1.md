# Phase 1 — Market-data integrity and point-in-time research journal

**Review state:** opt-in research implementation, not enabled in `main.py`, no capital authorization.
**Scope:** Binance Spot-like `aggTrade` frames. Files: `infrastructure/market_data_integrity.py` and `tests/test_market_data_integrity.py`.

## Current event path and boundary

The default system continues to run `BinanceWebSocketStreamer` ->
`md.aggTrade` / `md.depth` -> `MarketDataPipeline` -> `md.ticker` /
`indicator.volume_delta` / `indicator.obi` -> Predictor. These sources
have **not** been routed through the journal. That means the default
pipeline is not yet certified point-in-time or gap-free.

The research journal is a **separate offline admission API**. It
validates complete aggregate-trade payloads (`e,s,a,E,T,f,l,p,q,m`),
treats `a` as a per-symbol aggregate-trade ID within an explicit
market/source scope, enforces strictly contiguous IDs *from its first
observed baseline*, rejects inconsistent same-ID frames and stores a
single canonical immutable copy of accepted frames with SQLite WAL,
`synchronous=FULL`, transactionally updated sequence cursors and an
append-only operational incident table. `received_at_ms` is the
availability timestamp used for replay. Exchange `E`/`T` timestamps
are recorded but **never substituted for first observed receipt time**.

**Critical difference:** a first observed ID does NOT prove historical
completeness. If an ID is skipped, the journal records GAP and keeps
the cursor at the last admitted ID; a later ID is still blocked until
the missing range is supplied. Out-of-order unseen IDs, changed
same-ID content, stale or future-dated events are rejected. Duplicates
reuse their original stored receipt time. Stored content fingerprints
are checked during replay, but these hashes are not signatures and do
not authenticate Binance or protect against someone rewriting the
database and hashes together.

## Operator-free offline example

```python
from core.mode import QuantMode
from infrastructure.market_data_integrity import OfflineMarketIntegrityJournal

journal = OfflineMarketIntegrityJournal(
    "research/market-integrity.sqlite",
    market="binance_spot",
    mode_provider=lambda: QuantMode.SIMULATION,
)
result = journal.ingest_agg_trade(
    {
        "e": "aggTrade", "E": 1780000000000, "T": 1780000000000,
        "s": "BTCUSDT", "a": 501, "f": 501, "l": 501,
        "p": "50000.00", "q": "0.01", "m": False,
    },
    source="binance_public_simulation",
    received_at_ms=1780000000100,  # offline fixture only
)
assert result.accepted
records = journal.replay_as_of(1780000000100)
```

In production-like capture, **never pass an arbitrary/backdated**
`received_at_ms`; the default method reads the local receipt clock.
The research caller must be responsible for verifying NTP drift,
upstream transport identity and raw frame provenance. The source
label is a caller assertion, not proof of actual exchange connectivity.

The accepted `event_id` can be passed to the already existing opt-in
`Predictor.publish_for_market_event(event_id, frame, decision_factory)`,
which binds one source event to one decision. A strategy that consumes
a window containing multiple events instead needs a **separate durable
decision-window identity** and the precise set of input event IDs.
Simply passing the latest aggregate-trade ID as the whole inference
window is not a certified strategy-input lineage.

## Replay contract

`replay_as_of(cutoff_ms)` selects **only** events whose first receipt
time is at or before the cutoff, ordered by durable ingestion order,
not by exchange timestamp. The journal does not invent missing frames,
rewrite source timestamps, promise a historical exchange feed, or
advance the sequence cursor on quarantine. The incident list remains
available for audit, including after recovered gaps (incident retention
is distinct from current-gap status).

## Important omitted contracts

1. **Partial order-book data is not covered.** `lastUpdateId` from a
   partial depth snapshot can jump. It is not a simple +1 sequence
   stream. A future implementation must choose diff depth events
   (`U`,`u`) plus validated REST snapshot sync, or explicitly label
   top-N snapshot observations as non-contiguous. Do not apply this
   aggregate-trade journal to partial depth.
2. **Exchange-provenance and session scope:** verify Spot versus Futures,
   symbols and stream names from actual configured endpoints, reconcile
   REST/WebSocket overlap, reconnect/session changes, transport outage
   periods, missing bursts and de/listings. Current market/source
   labels are not authenticated.
3. **No default pipeline interception.** The legacy raw EventBus and
   derived indicators can still see duplicates or late frames.
   Integrate a single verified ingress *before* all raw subscribers
   only after downstream backpressure and durable replay semantics
   have been designed. Persisting a frame then losing the in-process
   bus queue must not cause unrecoverable dropped derived updates.
4. **Clock quality:** timestamp guards are a heuristic, not NTP/PTS
   verification. Add measured local clock uncertainty and upstream
   exchange-time calibration.
5. **No universal sequence model:** candles, funding/OI, macro release
   revisions and different exchange feeds each need source-specific
   provenance and point-in-time availability policies.
6. **No capital permission:** an accepted market tick is not evidence
   of a safe execution strategy, broker reconciliation or model alpha.
   REALITY use is explicitly denied by the journal.

## Next gated steps

Build dedicated tests and an opt-in adapter for original Binance
frames that records receipt timestamps at socket ingress before any
SIMULATION overlay; establish a durable delivery cursor separate from
the raw-event journal, then develop validated depth synchronization.
After that, add a stable input-window manifest for Predictor and
independent checks that simulated exchange data cannot be portrayed
as production trading evidence.

The regression suite exercises duplicate IDs, first baseline,
gap quarantine and backfill, changed payload, scope isolation,
out-of-order inputs, strict timestamps, point-in-time replay, database
restart, concurrent workers, rollback and mode isolation. No live
network, broker connection, exchange credentials or trading orders
are required.


## Research delivery cursor — durable projections, not EventBus ACK

`infrastructure/market_delivery_cursor.py` now adds the opt-in
`OfflineMarketProjectionCursor`, backed by the **same local SQLite file**
as the verified market journal. It is not registered by `main.py` and
requires an explicit `QuantMode.SIMULATION` provider from the journal.

Each isolated `consumer` has an independent cursor and at most one
active, renewable-on-expiry lease. `claim_next` selects the first
unprocessed event in durable `ingest_id` order, honoring first-receipt
`available_at_ms` and refusing to skip an earlier event that is not
observable yet. The lease token is randomly generated and kept in
SQLite. Worker restart or timeout can reissue the **same source event**
under a **new** token, fencing the previous token.

`commit_projection(claim, projection)` requires an unexpired valid
lease, revalidates the original immutable market frame and ensures
the cursor is at the precise next event. It then writes a canonical
finite-JSON projection with SHA-256 fingerprint, advances the cursor,
and deletes the lease in **one `BEGIN IMMEDIATE` transaction**.
An SQLite insertion or cursor write failure rolls everything back.
`projections(consumer)` verifies both the source row and durable
projection fingerprint and checks source identity/receipt timestamp
against the stored projection metadata.

These guarantees are deliberately narrower than exactly-once
end-to-end data delivery. The projection callback MUST NOT create an
external side effect: a crash after external transmission but before
`commit_projection` can cause that action to repeat. No EventBus
publication, downstream consumer receipt, derived indicator update,
or broker order is acknowledged by this cursor. To publish persisted
projections externally, a **separate durable outbox and independently
idempotent downstream consumer** are still required; neither currently
exists for market-derived projections.

Example:

```python
from core.mode import QuantMode
from infrastructure.market_data_integrity import OfflineMarketIntegrityJournal
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor

journal = OfflineMarketIntegrityJournal(
    "research/market-integrity.sqlite",
    mode_provider=lambda: QuantMode.SIMULATION,
)
consumer = OfflineMarketProjectionCursor(journal)
claim = consumer.claim_next("price-projection")
if claim is not None:
    # Pure, deterministic local projection. NO publishing/trading here.
    result = {"price": claim.event.frame["p"]}
    receipt = consumer.commit_projection(claim, result)
    # Other processes may read consumer.projections("price-projection").
```

**Tests** in `tests/test_market_delivery_cursor.py` cover crash before
commit, expired lease takeover and stale-worker fencing, cross-thread
claim contention, independent consumers, point-in-time cursor order,
transaction rollback, restart, tampering, invalid payloads, mode
changes and preservation of pending source records. Operational
health, retry storms, consumer lag and delivery dead-letter handling
remain to be designed before activating any default pipeline.

### Incident and gap handling remains fail-closed

The source journal's GAP and OUT_OF_ORDER incidents are retained as
an audit history. This projection cursor consumes **only accepted**
events; it does not repair missing exchange frames or certify
continuity for other streams. In particular, `md.depth` partial book
snapshots still need an exchange-appropriate snapshot/diff
synchronization protocol, not simple trade-ID increments.
