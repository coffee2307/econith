# Phase 1 — public Spot acceptance and fail-closed cutover preflight

**Status on Draft PR #28:** code and offline tests, not independently
approved for default runtime. Binance Spot public WSS/REST sessions remain
strictly opt-in. The tested offline research path has **not** replaced the
legacy `BinanceWebSocketStreamer` -> `md.aggTrade/md.depth` EventBus
publish/subscribe graph.

## Explicit manual real public observation

Run `python -m ci.phase1_public_spot_acceptance` from the repository root.
Without `--allow-public-market-data` it returns nonzero BEFORE creating files
or opening any network socket. Operator supplies an **absolute** `--output-dir`
for an isolated public-market research SQLite DB and JSON observation report.

When explicitly enabled, the CLI:

1. Uses certificate-verified HTTPS GET
   `https://data-api.binance.vision/api/v3/time`, with redirects disabled
   and environment proxies disabled, to measure approximate local wall-clock
   offset and monotonic RTT. Rejects high latency/clock-offset observations
   BEFORE opening the WSS stream. RTT/2 is a *heuristic* uncertainty range,
   not NTP synchronization or a trusted signature for trade timestamps.
2. Uses only `QuantMode.SIMULATION`. Creates a durable worker fence, bounded
   retry controller and original-public-capture journal in the chosen output
   directory. Explicitly opens market-data-only
   `wss://data-stream.binance.vision/stream` and read-only
   `GET https://data-api.binance.vision/api/v3/depth` through the vetted
   opt-in adapter. There are **no API keys or order endpoints**.
3. Saves the exact original combined-stream and REST snapshot bytes with
   first local receipt clocks in SQLite, validates depth U/u snapshot bridge
   and complete as-of replay, and writes a JSON audit report including
   verified bounded session status, number of captured frames,
   aggregate trade count, depth sequence state, retry/outage record and
   coarse time check. No market data are sent to EventBus or trading.
4. Returns a nonzero exit code if no synchronized capture was obtained. A
   successful bounded session **never** certifies exchange-wide completeness,
   an outage-free extended capture period, an authenticated individual market
   event or financial returns.

Example for a user-controlled research environment (explicit network consent):

```powershell
python -m ci.phase1_public_spot_acceptance `
  --allow-public-market-data `
  --output-dir "$env:TEMP\econith-phase1-public" `
  --symbol BTCUSDT `
  --max-frames 500 `
  --timeout-s 45
```

The repository also defines
`.github/workflows/phase1-public-spot-acceptance.yml` with **only**
`workflow_dispatch` (manual) trigger, explicit boolean consent input,
read-only GitHub permission, a temporary runner and three-day private
evidence artifact retention. It has NO push, PR, schedule, service deploy or
exchange credential step. **GitHub workflow_dispatch does not become
user-visible/runnable solely by adding the workflow on this Draft PR branch**:
GitHub registers it only once the workflow definition is on the repository's
default branch. Do not merge solely to enable this workflow; obtain the
usual code-review and branch approval first. Until then, the CLI and mocked
offline regression tests are the only implementation evidence.

Official protocol reference:
- https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md
- https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md

## No false verified-only cutover

`infrastructure/spot_verified_cutover_gate.py` and `main.py` now provide
a **startup preflight** `ECONITH_SPOT_INGRESS_POLICY`:

- `legacy` (default): existing behavior, explicitly **not** verified.
- `verified_only`: raises `VerifiedIngressNotReady` before starting
  database initialization, engine, market streamer or other async services,
  because the app still wires the old `depth20` partial snapshot publisher.
- Any unrecognized value: **fail closed**, never silently revert to legacy.

This is a guard against false statements and dangerous premature cutover,
NOT a verified-only runtime implementation. To actually enable one later:
remove the legacy raw producer; wire original-capture-first durable
verified admissions; require all consumers to read receiver-owned durable
receipts; enforce the same rule for *every* market-data publisher; validate
real reconnect outages; then add automated end-to-end tests that prove no
unverified EventBus bypass. Do not simply change the flag to
`verified_only` in production: **it will fail startup by design**.

## Acceptance checklist

| Criterion | Current evidence | Status |
|---|---|---|
| Offline raw byte capture and Spot U/u replay | Extensive unit and E2E SQLite tests | Implemented |
| Crash-safe receiver-owned ACK across process and DB | Subprocess and three-DB E2E tests | Implemented in offline opt-in path |
| Bounded TLS WSS+REST client with local clock guard | Opt-in code and offline mocks | Implemented, not exercised live here |
| Real Binance market session + bounded clock-offset report | Explicit manual runner prepared | **Not executed / evidence unavailable** |
| Authenticated real IPC/prod broker receiver | No network RPC service in research module | **Not implemented** |
| End-to-end verified-only cutover of all raw consumers | Fail-closed guard blocks premature flag | **Not implemented** |
| Main merge and production deploy | Not authorized | **Not performed** |

Research code alone cannot justify a 100% Phase 1 claim. The engineering
acceptance percentage is a subjective milestone, not code coverage. CI green
on the draft branch is required before review, and real live capture/cutover
need separate documented evidence and approval.
