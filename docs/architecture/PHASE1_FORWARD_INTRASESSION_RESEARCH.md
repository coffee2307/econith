# Phase 1 — in-session verified public Spot RESEARCH shadow

**Not a continuous certified data feed. Not a strategy, paper/live execution
pipeline, production deployment, arbitrary EventBus delivery guarantee or
24/7 admission.** This capability exists only in manually selected,
SIMULATION-only research processes, never default `main.py`.

The prior bounded public microbatch and soak published research data only
AFTER an entire source session was sealed and verified. This experimental
module adds a deliberately narrower in-session read-only data plane:
`infrastructure/spot_forward_verified_research.py`.

## Exact ordering for each forward-eligible original

1. The existing `OptInSpotIngressAdapter` stamps and COMMITs the unmodified
   WebSocket envelope or REST depth snapshot into `original.sqlite`.
   Queue overflow and any SQLite error are terminal, not silent data loss.
2. Its `on_persisted` callback invokes `IncrementalSpotShadowValidator`
   which **re-reads the exact source row** by capture ID and validates local
   clock, source identity, trade IDs and snapshot/diff sequence.
3. **Before** a complete REST snapshot + diff bridge exists, a trade is NOT
   published. It is counted as `pre_sync_trades_not_forwarded`. A raw diff
   by itself is NEVER mislabeled as a complete depth20 snapshot.
4. After synchronization, only the latest as-of, book-derived full snapshot
   is eligible for `md.depth`. Its `available_at_ms` must be at or earlier
   than the current committed record's local receipt. Later genuine depth
   changes produce new observations. An unchanged book ID is not re-emitted.
   Trade forwarding additionally fails closed when the last synchronized
   book availability exceeds the explicit freshness budget (default 2500ms,
   configurable 100–30000ms), rather than silently pairing a fresh trade
   with an obsolete order book.
5. A bounded canonical envelope includes raw original SHA-256, original
   capture ID, original local availability, topic and admitted payload.
   A **separate Python interpreter**, authenticated over an ephemeral HMAC
   pipe, durably COMMITs its own SQLite receiver receipt. Parent checks the
   signed exact receipt before admitting any raw event.
6. The opt-in EventBus accepts raw publications only with its capability;
   derived ticker, volume delta and OBI require a distinct verified-derived
   publisher capability. Only the reviewed `MarketDataPipeline` is
   registered. A primary research observer plus two named read-only research
   observers each maintain independent WAL/FULL durable derived receipts.
   Every emitted event waits for graph completion and durable receipt proof.
   There are no execution or account subscribers in this graph.
7. At session end, the existing shadow cross-checks the complete sealed
   original, and receiver/derived receipt counts are independently checked.
   Only then is a `ForwardSpotResearchStatus(session_complete=True)`
   returned. Source failures mean no acceptance report. Already admitted
   **read-only** observations cannot be globally rolled back.

The transport reader is **bounded**, but its connection remains open during
verified source-to-receiver admission. The reader and callback may run in
parallel; a stalled receiver leads to backpressure or timeout, and the session
fails closed. Performance is NOT optimized: each admitted event currently
requires a separate child Python interpreter; this prototype is unsuitable for
24/7 high-frequency market loads. First source session observation also
does not prove missing prior exchange trades or gapless historical coverage.

## Manual public-only sample

In an authorized ECONITH checkout with approved Python dependencies:

```powershell
Set-Location F:\econith
python -m ci.phase1_forward_spot_research `
  --allow-public-market-data `
  --output-dir "$env:TEMP\econith-forward-spot-new-01" `
  --max-frames 160 --timeout-s 45 --queue-capacity 128
```

Choose a new absolute evidence directory each time. This program reads
only Binance public TLS/WSS Spot trade/depth and public GET time/snapshot
endpoints, requires the explicit opt-in and a coarse public clock preflight,
and never uses account credentials. It does not automatically reconnect,
resume unfinished forwarding, or start as part of the main app. Injections in
CI use `offline_fixture` and **do not** claim real TLS peer validation.

## Required future gates — retain until separately proven

- Durable raw-to-forward **producer outbox** and recovery of a crash between
  the independent receiver COMMIT and EventBus derived observer COMMIT;
  current restart must use a new original source session and quarantine
  prior uncertain execution/forward state.
- Batch/persistent HMAC receiver transport and bounded throughput/load tests
  for continuous market traffic; no asyncio task cancellation may be
  interpreted as undoing a committed receiver transaction.
- Real public network operator E2E with fault injections, source stale-clock
  checks, resource pressure, random disconnections and physical host restart;
  quantify observed coverage gaps without silent backfill.
- Full time-aware book snapshots, trade-decision as-of evidence and
  independent audit review over long-running feeds.
- Review all alternate raw/derived source publishers and effectful consumers
  before any default application cutover. Current legacy `main.py` stays
  unchanged and exchange order authorization stays disabled.
- Broader original Phase 1 dataset coverage (historical OHLCV, multiple
  symbols, revisioned macro vintages and optional derivatives fields).

No partial or historical research metrics can be presented as realized PnL
or verified profitability. The full Phase 1 remains OPEN.
