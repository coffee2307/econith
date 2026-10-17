# ECONITH Phase 1 — Evidence and exit-gate decision (2026-10-18)

**Decision:** Core market-data *offline research* acceptance is verified with
bounded public data and subprocess ACK. **Overall Phase 1 remains OPEN**:
the default application is *not* verified-only and must not be called
production-ready or be treated as real-money trading authorization.

## Evidence matrix (operator-supplied console reports; exact review HEAD)

| Experiment | Measured evidence | Interpretation |
|---|---|---|
| Raw public Binance BTCUSDT TLS WSS + REST snapshot | 500 raw WS (184 aggTrade, 316 depthUpdate), 1 REST snapshot, 501 persisted originals; SYNCED book | A short **bounded** actual public-market observation passed; not a complete historical market feed |
| Local first-receipt clock plausibility | Observed offset +439ms, RTT 922ms, estimated 461ms RTT/2 uncertainty | Approximate offset only; no signed event timestamps |
| Original capture → verified market → independent receiver DB | 501 replayed, 184 promoted, 184 lineage mappings, 184 producer ACKs, 184 receiver receipts | Real saved original bytes passed the offline as-of integrity and idempotent local research delivery path |
| HMAC-authenticated local OS receiver process | Same 184/184 ACK results, `cross_process_authenticated_transport=true`, `HMAC_AUTHENTICATED_LOCAL_SUBPROCESS_PIPE_ONLY` | Validated **local-process** authenticated transfer, not remote TLS/broker |
| Two real public WebSocket sessions with injected disconnect | 11 local tests passed; first session FAILED `INJECTED_WS_DISCONNECT` with 81 original persisted records; second session SUCCESS, 500 WS frames + fresh REST snapshot, SYNCED book ID 101567598466; one restored outage | Verified reconnection with a new source session/snapshot and durable failure history |
| Historical market completeness after outage | `history_continuity_verified=false`, `coverage_verified=false`, `gap_imputed_or_backfilled=false` | **Correct FAIL-CLOSED result**: a new valid book does not retroactively recover trades never observed |
| Default app verified-only runtime | `default_runtime_cutover=false` | **NOT DONE**; an opt-in review branch's research modules cannot be mistaken for default runtime integration |

The operator's last successful result ran on
`a771504b3dad912d2df3127fd7a1c2e82a6cd657`, and was reported in
local PowerShell with exit code 0. The assistant inspected the operator's
transcript and JSON only; it did NOT directly inspect the private raw SQLite.

## Verified-only runtime cutover: exact outstanding work

The code actually executed on app startup remains:

- `main.py` calls `enforce_verified_only_spot_ingress()` **before** DB,
  engine and network initialization. Its `verified_only` policy correctly
  **rejects all startups**, because no complete runtime cutover exists.
- `main.py` constructs `BinanceWebSocketStreamer(engine.bus,engine.time)`
  and invokes `await streamer.start()`. The streamer publishes
  `md.aggTrade` and **partial `depth20`** `md.depth` events, potentially
  mock/simulation-overlaid; this is *not* an original REST snapshot +
  `depthUpdate` synchronized full-book verified source.
- `MarketDataPipeline` subscribes directly to these raw topics and
  publishes `md.ticker`, `indicator.volume_delta` and
  `indicator.obi`. Downstream signals can be reached without the offline
  capture integrity journal/receiver receipts. The EventBus optional
  `activate_verified_market_only()` guard is NOT enabled in `main.py`.

This makes the remaining work a **runtime architecture migration**, not a
missing unit test or an incomplete Binance public-data capture.

### Mandatory acceptance for closing overall Phase 1

1. **Define approved runtime data-plane scope:** offline-only research
   replay vs continuously ingesting public Spot feeds. They have different
   time and delivery semantics; never pretend a final snapshot was
   continuously available for earlier trades.
2. **Isolate execution:** ensure every verified-only market consumer runs in
   SIMULATION with capital/order routes disabled, including the registered
   execution bridge, and no REALITY/production escape while unapproved.
3. **Wire verified source first:** all source bytes must be durably captured
   and validated before any corresponding raw event reaches subscribers.
   A `depthUpdate` must never be mislabeled as `depth20`; publish a
   book-derived snapshot only when synchronized at that event's as-of
   availability. Restart gaps must invalidate the old book until a new REST
   bridge establishes a new session.
4. **No legacy bypass:** explicitly do **not** instantiate/start the legacy
   `BinanceWebSocketStreamer` in verified-only mode. All raw topic ingress
   must require the protected EventBus publisher and a verified lineage
   check, rather than a caller-provided source label or unchecked flag.
   Derived topics and any alternate market-data publishers also require
   an exhaustive source and subscriber inventory.
5. **ACK/retry and backpressure:** define durable per-consumer admission
   (with retry and dead-letter policy), local receiver receipt checks,
   subscriber exception behavior and restart recovery; quantify delivery
   gaps and refuse to silently replace original frames with synthetic data.
6. **CI and operator E2E:** test startup guard and verified-only selection,
   raw publisher bypass resistance, true source availability,
   derived indicator correctness, failure/reconnect, mode transitions,
   process crash, datastore recovery, and bounded replay of saved real Spot
   originals. Only remove the startup refusal when these are verified.
7. **Independent review:** keep Draft PR and require review before changing
   default market pipeline or merging. No credentials, live execution,
   production deployments or capital permissions are covered by Phase 1.

**Status for next engineering iteration:** exactly one aggregate open gate,
*reviewed verified-only runtime source/subscriber integration*. Reconnect
acceptance is already passed; repeat public capture only if a regression
requires new evidence. Labeling the full Phase 1 as 100% before completing
the runtime migration would make an unsupported operational claim.

**Separately**, if the project owners formally redefine Phase 1 to mean
*offline market research only*, the offline research sub-scope can be marked
complete based on operator evidence; default runtime verification and
production integration must then become explicit separate milestones.
Do not silently make that scope change.
