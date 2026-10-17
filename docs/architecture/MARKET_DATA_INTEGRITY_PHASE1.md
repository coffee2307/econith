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
