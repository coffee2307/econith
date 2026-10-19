# Phase 1 — controlled public Spot disconnection and conservative reconnect acceptance

The operator has already demonstrated (2026-10-18) that BTCUSDT Binance Spot
bounded public capture works and that the saved 501 original messages replay
to 184 verified aggregate trades, 184 source ACKs, and 184 authenticated
local-subprocess receiver receipts. The successful subprocess evidence
explicitly contains `history_continuity_verified=false` and
`default_runtime_cutover=false`.

## This additional acceptance is about lost-history honesty, NOT gap filling

`ci/phase1_public_reconnect_acceptance.py` deliberately ends the FIRST
public TLS/WSS capture after a bounded count of original frames using
`inject_disconnect_after_frames`. The error is a typed, persistent
`INJECTED_WS_DISCONNECT`. The controller must mark the old attempt FAILED
and create an open outage in its SQLite ledger. After controlled backoff,
a SECOND **fresh public TLS WebSocket session and independent REST depth
snapshot** must synchronize a new order book. The controller must mark the
new attempt SUCCESS and close the outage, but **never set historical coverage
to verified**.

Success means:
- Two distinct capture session IDs, both local original-event records retained
- First session has at least the specified original WebSocket frames and
  an explicit `INJECTED_WS_DISCONNECT` failure code, not SUCCESS
- The controller's offline outage is restored but
  `coverage_verified=false` and
  `history_continuity_verified=false` remain
- Second session's original transport bytes replay to a valid synchronized
  book with the second session ID, using a newly acquired REST snapshot
- No inferring or synthetic forward-filling of missing market events
- Zero private exchange credentials, user-account APIs, orders, strategies or
  production services

The disconnect is injected inside the local asynchronous stream reader while
the peer connection is using the standard public TLS/WSS transport. It is NOT
an exchange outage, ISP packet loss or proof of universal resilience. A
successful short finite test does not recover trades that the collector never
observed during the disconnect.

## Manually authorize read-only live market data

From the same detached Windows worktree (once CI has passed):

```powershell
$repo = "F:\econith"
$branch = "chore/phase0-inventory-hygiene-20261017"
$work = Join-Path $env:TEMP "econith-p1-20261017-225926"
$py = Join-Path $work ".venv\Scripts\python.exe"
git -C $repo fetch origin $branch
git -C $work switch --detach "origin/$branch"
Set-Location $work

& $py -m pytest tests/test_phase1_public_reconnect_acceptance.py -q
if ($LASTEXITCODE -ne 0) { throw "Reconnect regressions failed" }

$evidence = Join-Path $env:TEMP ("econith-p1-reconnect-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
& $py -m ci.phase1_public_reconnect_acceptance `
    --allow-public-market-data `
    --output-dir $evidence `
    --symbol BTCUSDT `
    --interrupt-after-frames 80 `
    --max-frames 500 `
    --timeout-s 45

Write-Host "EXIT CODE:" $LASTEXITCODE
$report = Join-Path $evidence "phase1_reconnect_acceptance.json"
if (Test-Path $report) { Get-Content $report -Raw }
```

Expected `acceptance`:
`PASSED_CONTROLLED_RECONNECT_WITH_UNRECOVERED_HISTORY_GAP`.

If the public time check, TLS, stream reader or snapshot fails, keep the
report and existing SQLite evidence; do not disable certificate checks,
alter original stored frames or pretend failed attempts passed. This tool is
opt-in, not run automatically on CI, app startup, deployment, or cron.

## Remaining gate: actual application verified-only ingress cutover

The **default** `main.py` still builds `BinanceWebSocketStreamer`, which
publishes raw `md.aggTrade` and partial `md.depth` into EventBus and can
apply SIMULATION overlays. The optional EventBus raw-topic capability gate
does not change default wiring. Requesting
`ECONITH_SPOT_INGRESS_POLICY=verified_only` MUST still refuse startup
until a separately reviewed runtime publisher exists that:
- validates and journals each original transport frame before consuming it,
- replays snapshot/diff depth correctly without a look-ahead,
- durably receives and verifies per-consumer local results before publishing,
- avoids raw legacy publisher/partial-depth bypass for ALL relevant consumers,
- guarantees fail-closed behavior on reconnect/stale clocks/missing inputs.

Absent the above evidence, `100% Phase 1` would be overstated. It is
acceptable to call the *offline research capture & delivery core* ready
while retaining strict cutover blockers, but not to call the entire
default application verified-only.
