# Phase 1 — post-COMMIT incremental Spot shadow + acknowledged research EventBus

**Scope:** opt-in public BTCUSDT market-data-only SIMULATION, isolated Draft PR
#28. No default live ingestion cutover, account API keys, real trading,
production deploy, scheduler or main merge.

## Additional hardening

### Incremental shadow after durable original capture

`OptInSpotIngressAdapter.capture_open_stream(..., on_persisted=...)` and
`capture_public_once(..., on_persisted=...)` now have a strictly optional
callback. The adapter verifies and commits the exact original WSS
`aggTrade`/`depthUpdate` or REST snapshot bytes to the SQLite WAL journal,
rereads the committed `capture_id` through
`OfflineSpotCaptureJournal.verify_captured_record`, then awaits the callback.

- No callback fires for data that did not successfully COMMIT. An invalid
  fingerprint, wrong session/symbol, malformed source or stale receipt is
  denied before the callback.
- The callback runs within the single writer while the socket reader feeds
  a **bounded** FIFO. Slow processing causes a terminal
  `QUEUE_BACKPRESSURE` rather than silent frame dropping. No automatic
  unbounded queue, background daemon or lossless long-term live claim.
- `IncrementalSpotShadowValidator` requires source records to match the
  committed row, verifies nondecreasing local receipt and strictly rising
  original capture IDs, observes contiguous Spot `aggTrade` IDs (except
  identical duplicates), incrementally applies original Spot diff-depth and
  exactly one REST snapshot via `OfflineSpotDepthSynchronizer`. An update-ID
  gap, corrupted record or invalid snapshot permanently poisons the session:
  a distinct WebSocket session plus a fresh snapshot is mandatory.
- At session closure, `shadow.finish()` compares its incremental counts
  and book update ID with independent whole-session original-byte replay.
- The public bounded microbatch worker attaches a new shadow to *each*
  actual network capture/reconnect attempt, then checks its results again
  after the existing original→market→independent HMAC receiver ACK gate.
  Report flags distinguish actual TLS sessions with an incremental shadow
  from injected offline transport fixtures.
- **Critically, the shadow itself has `publisher_events=0`:** it cannot
  place orders or publish a provisional book to research consumers. The
  validated data remains delayed until the sealed source and independent
  receiver receipts have been fully checked.

### Verified EventBus subscriber completion barrier

The opt-in `EventBus.activate_verified_market_only(max_inflight=16)`
capability now supports
`VerifiedMarketPublisher.publish_raw_confirmed(..., timeout_s=...)`.
For raw research topics, it limits the number of concurrent unconfirmed
deliveries and does **not** report success until every matching raw subscriber
coroutine has returned normally.

- If no raw subscriber exists, one handler throws, the bus stops, a mode
  changes to REALITY, or admission exhausts credits, the confirmed path
  refuses an ACK. A timeout is an **unknown outcome**, not an assertion
  that a handler's side effects were rolled back. The credit remains held
  until the original handler actually completes.
- `EventBus.wait_verified_idle()` blocks on all queued raw **and derived**
  handlers and refuses to pass if any protected research bus handler failed.
  This closes a previous weakness where EventBus logged handler exceptions
  but the microbatch acceptance only counted derived messages.
- The bounded public research worker and the historical verified-only
  ASGI branch now use confirmed raw delivery and the derived-graph barrier.
  Legacy EventBus usage remains unchanged by default.
- **This is an in-memory coroutine-completion barrier, not durable
  per-consumer exactly-once.** It does not atomically commit a subscriber's
  effects to a separate database, nor does it establish multi-machine
  broker authentication or replay semantics. Independent source/receiver
  *projection* SQLite receipts remain the durable gate, and market-trading
  consumer activation stays disabled.

## Explicit remaining exit gates

This work catches source corruption closer to arrival and surfaces
research-consumer errors before PASS. It does **not** prove low-latency
continuous, gap-free Binance streaming: the externally visible research
signals still arrive only after each bounded session is sealed and its
independent receipts are durable. A true 24/7 verified live runtime would
require independently reviewed streaming provenance and per-consumer
transactional ACK/recovery, testable saturation/backpressure metrics,
prolonged failure/soak testing, and complete default legacy-source bypass
elimination. `continuous_live_market=false`,
`history_continuity_verified=false` and zero orders remain correct.

Recommended review sequence: CI on the exact Draft HEAD, offline
`pytest tests/test_spot_incremental_shadow.py
tests/test_market_verified_eventbus.py
tests/test_spot_public_verified_microbatch.py -q`, and only then one
short explicitly authorized public read-only run in a detached research
worktree. Do not use production secrets or modify the existing raw capture.
