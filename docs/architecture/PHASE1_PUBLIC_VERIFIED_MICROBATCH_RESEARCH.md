# ECONITH Phase 1 — finite public verified research microbatches

**Status:** Opt-in SIMULATION-only research utility on Draft PR #28.
This is a finite public-market transport acceptance path, **not** an
always-on, gapless low-latency production live market feed.

## Proven source-to-subscriber ordering per completed batch

`infrastructure/spot_public_verified_microbatch.py` runs 1–4 bounded
read-only Binance Spot TLS WSS/REST sessions under a SQLite generation-fenced
reconnect controller. For **each** successfully closed session it:

1. Preserves the original combined-stream `aggTrade` and `depthUpdate`
   transport bytes plus the original REST depth snapshot and first local
   receipt times in a SQLite WAL journal.
2. Replays the **same** closed session and independently checks aggregate
   trade ID continuity and snapshot/diff order book synchronization;
   corrupt/partial/unclosed sessions fail before admission.
3. Copies the original SQLite as **read-only source** into a NEW per-cycle
   research directory, promotes only verified `aggTrade` events into an
   independent market integrity journal, verifies lineage and creates durable
   producer projections. Each projection is accepted by a separately spawned
   HMAC-authenticated local receiver Python process with its own SQLite DB;
   no raw event is permitted into the event bus before **every** receipt
   is committed and verified.
4. Uses a separate SIMULATION EventBus with
   `activate_verified_market_only()`, and registers **only**
   `MarketDataPipeline`. Replays research trades with original
   `available_at_ms` metadata and publishes one final synchronized
   snapshot-derived top-20 consumer view. Checks exact ticker,
   volume-delta and OBI subscriber outputs, rejects fake direct
   publisher attempts, and saves a cumulative local JSON audit report.

Each session is finite and publication **lags** the original observation by
capture duration, snapshot verification and receipt processing. As-of
availability is preserved but publishing is delayed. The final book is
**never** asserted to have existed at earlier trade receipt times. Gaps
between bounded WebSocket sessions are explicitly **unobserved** and
historical continuity is `false` by design.

### Explicit manual Windows probe

No public market socket is opened without the exact
`--allow-public-market-data` CLI flag. Do not run against an existing
production database, real-money trading environment or public server.

```powershell
$repo = "F:\econith"
$branch = "chore/phase0-inventory-hygiene-20261017"
$work = Join-Path $env:TEMP "econith-p1-20261017-225926"
$py = Join-Path $work ".venv\Scripts\python.exe"

git -C $repo fetch origin $branch
git -C $work switch --detach "origin/$branch"
Set-Location $work

& $py -m pytest tests/test_spot_public_verified_microbatch.py -q
if ($LASTEXITCODE -ne 0) { throw "Microbatch tests failed" }

$evidence = Join-Path $env:TEMP ("econith-p1-public-microbatch-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
& $py -m ci.phase1_public_verified_microbatch `
  --allow-public-market-data `
  --output-dir $evidence `
  --symbol BTCUSDT `
  --cycles 2 `
  --max-frames 160 `
  --timeout-s 45 `
  --max-attempts 2
Write-Host "EXIT CODE:" $LASTEXITCODE
$report = Join-Path $evidence "phase1_verified_public_microbatch.json"
if (Test-Path $report) { Get-Content $report -Raw }
```

Expected after successful operator capture:
`acceptance=PASSED_BOUNDED_PUBLIC_VERIFIED_MICROBATCH_RESEARCH`,
`completed_cycles=2`, `source_sessions_distinct=true`,
producer/receiver receipt counts matching the original verified trades
**for each** completed session, `public_tls_peer_confirmed=true` for
the normal adapter, `orders_submitted=0`,
`execution_started=false`, `legacy_streamer_started=false`.
`continuous_live_market=false` and
`history_continuity_verified=false` are **required and correct**.

On any exception, retain the raw SQLite and `microbatch_progress.json`
(if any). Never mark an unsuccessful or unfinished cycle as admitted.
The command exits nonzero and sanitizes console exception text.

## Scope boundary for Phase 1 closure

This utility bridges a real public transport to an authenticated durable
receiver and working market-data subscribers **without using fixtures** when
manually invoked. It does NOT fully replace the legacy continuous streamer
in `main.py`, and does not guarantee tick-by-tick publication, sustained
24/7 operation, complete exchange history after outages, or durable
EventBus per-handler ACK. The default `ECONITH_SPOT_INGRESS_POLICY=legacy`
remains unchanged. The isolated `verified_only` main-ASGI branch remains a
historical replay of a specifically configured **closed** source.

Formal full Phase 1 approval requires either:
- Further engineering of an independently reviewed low-latency
  continuous verified ingress with per-consumer durable ACK and real
  backpressure under failures; or
- An **explicit** project decision defining Phase 1 as verified offline
  research + manually authorized bounded public observations and moving
  continuous infrastructure into a separate phase. Do not silently
  redefine the acceptance scope to claim completion.

No credentials, live orders, main merge, CI schedule or production deploy
are introduced by this module.
