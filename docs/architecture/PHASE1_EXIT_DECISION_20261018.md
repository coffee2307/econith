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

- `main.py` checks `enforce_verified_only_spot_ingress()` before DB,
  engine and network startup. Invalid/incomplete `verified_only` configuration
  fails closed. **A configured closed historical replay is now supported**
  in an isolated SIMULATION ASGI lifespan branch; it does NOT initialize
  engine, CCXT, order bridge, HTTP mutation routes or legacy streamer.
- **Default `legacy` startup** still constructs `BinanceWebSocketStreamer`,
  publishes raw `md.aggTrade` and unverified partial-`depth20` `md.depth`,
  and cannot be considered an approved verified-only runtime.
- The **opt-in historical research branch** now uses guarded raw and derived
  `EventBus` publication, authenticated source/receiver ACK and independent
  derived observer receipts. The **default legacy** market pipeline still
  bypasses these research-only source and subscriber proof gates.

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

## Follow-up research evidence and deferred debt (2026-10-18)

The protected historical research path and finite public microbatch path now
include **two independent named read-only derived subscribers** (audit and
monitor) in separate SQLite receipt ledgers. Each has source-bound, per-topic
durable cursors and idempotent recovery tests. The opt-in research graph also
guards `md.ticker`, `indicator.volume_delta` and `indicator.obi` publication
using a distinct pipeline capability; the ordinary EventBus remains legacy.

The new `infrastructure/spot_verified_soak.py` and
`ci/phase1_verified_spot_soak.py` implement an opt-in **finite supervised
sealed-market research soak** with per-round public clock preflight, at most
24 rounds, separate source sessions, HMAC independent receiver/producer ACK,
named subscriber receipts, atomic checkpointed evidence, SHA-256 report
integrity checks and a single-writer orphan-sensitive process lock.
All reports explicitly state `continuous_live_market=false` and
`history_continuity_verified=false`. Full details are in
`PHASE1_FINITE_VERIFIED_SPOT_SOAK.md`.

**Explicit remaining Phase 1 acceptance debt:**

- **P1-A — continuous market-data source:** create a genuinely long-running
  verified source/consumer runtime, not consecutive sealed captures.
  Enforce fresh snapshot after each reconnect, event-as-of ordering, lag,
  clock plausibility and no fabricated historical coverage.
- **P1-B — long-running backpressure:** verify bounded queue/CPU/RAM/disk,
  timeouts and consumer liveness under real market traffic and stalled
  subscribers, including 24–72h operator soak and power-loss restart.
- **P1-C — subscriber completeness:** inventory all original and derived
  producers/subscribers. Three approved research-only durable observers
  do not make the arbitrary EventBus graph transactional or recoverable.
  Define retry/dead-letter/idempotency for any future effectful consumer.
- **P1-D — reviewed default runtime cutover:** isolate/remove legacy source
  bypass, separate `REALITY` data mode from exchange execution authority,
  prove correct API/UI exposure, and retain a fail-closed unapproved path.
- **P1-E — original institutional data coverage:** multi-symbol OHLCV,
  timestamp/de-list/precision tests and conditional funding/open interest
  and macro announcement-vintage provenance have not been completed.
- **P1-F — dependencies/legal:** remaining gitlink ownership and GPL/MIT
  boundary require owner/source/legal review separately from CI.
- **P1-G — real operator acceptance:** validate a new original public Binance
  capture with reproducible versioned evidence, source clock, resource
  metrics and physical host crash recovery. CI fixtures alone do not
  prove real TLS availability or production-grade continuous ingest.

**Exit decision unchanged: Phase 1 overall OPEN.** Do not merge PR #28
or authorize broker execution or capital on this research-only evidence.
