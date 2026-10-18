# Phase 1 — Original eligibility rescan & protected forward ASGI preview

**Branch:** Draft PR #28. **Purpose:** close the finite-session raw→forward
intent reconstruction gap and expose a *strictly opt-in*, limited, read-only
public Binance forward research graph through `main.py`. Neither feature
authorizes capital, exchange orders, default source switching, production
cutover, 24/7 availability or gap-free exchange history.

## Source recovery: exact durability and temporal contract

`infrastructure/spot_forward_eligibility.py` performs an original,
capture-ID-ordered rescan of `OfflineSpotCaptureJournal` with its
**same-COMMIT** `offline_forward_capture_intents` metadata. Every row
is independently re-read and verified for original SHA, source ID and
`received_at_ms`. It runs `IncrementalSpotShadowValidator` and
`OfflineSpotDepthSynchronizer` from a **fresh book state**, not the final
snapshot from the end of the session.

Eligibility is reconstructed only when originally available: the first
observed trade before the REST snapshot+diff bridge cannot use a later book;
duplicate aggregate IDs do not become new trades; changed synchronized book
IDs become snapshot-derived `md.depth`; stale book, incorrect depth
sequence, invalid source or local clock fail the **entire rescan** closed.
The exact canonical payload, original SHA and original ingest ID are
reconstructed independently of the already staged outbox.

`recover_staged_forward_spot(..., reconstruct_missing=True)` compares
**all existing** immutable envelopes with this independent rescan BEFORE
modifying the producer outbox. Any conflict aborts. Missing eligible intents
are staged in ascending original capture order; the existing local
HMAC-authenticated independent receiver and three SQLite derived observers
then receive the same source-bound envelopes. Recovery is idempotent and
protected by the exclusive local producer/recovery lock.

Recovery without this explicit flag remains staged-only. If a process died
before the *first* eligible intent was staged, no receiver DB may exist:
its initial creation is permitted only when the recovered staged set is
**entirely new and there were zero pre-existing intents/ACKs**. An existing
receiver-owned DB that is later deleted is NEVER silently replaced.

Even when the entire durable original prefix is deterministically rescanned,
a killed source session remains incomplete, and unseen trade events during
an outage are unknowable. The report intentionally retains
`source_coverage_certified=false`, `session_certified=false`, while
`original_eligibility_rescan_verified=true` attests the *persisted original
prefix*, not the complete external market.

### Manual recovery — only after operator-confirmed process death

```powershell
Set-Location F:\econith
python -m ci.phase1_recover_forward_outbox `
  --research-recover `
  --reconstruct-missing `
  --output-dir "$env:TEMP\<existing-forward-evidence-root>" `
  --session-id "<exact rec_... session identifier>" `
  --max-envelopes 2000 --max-book-staleness-ms 2500
```

This is **not** an executable copy-paste example until the operator replaces
both placeholder identifiers with real existing evidence. Do NOT delete an
orphan `.forward_producer.lock` unless the producing process is known to
be stopped and the source state has been reviewed. Never reconstruct with
different freshness parameters from those used for the original run;
conflicts are intentionally fatal. No public socket, account or order route
exists in the recovery command.

## Forward market preview in the ASGI runtime

The `verified_forward_preview` policy has a separate, opt-in branch in
`main.py`. It is not the default `legacy` policy and not the existing
`verified_only` closed historical replay branch. Its startup requires all:
- `ECONITH_SPOT_INGRESS_POLICY=verified_forward_preview`;
- `ECONITH_SPOT_FORWARD_PREVIEW_CONSENT=PUBLIC_MARKET_DATA_RESEARCH_ONLY`;
- `ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR` pointing to a **fresh, absolute,
  absent, non-symlink** local research evidence directory.

The ASGI lifespan runs the coarse public Binance clock preflight *before*
a source task starts, then launches **one finite bounded session** using the
existing original commit / source integrity / HMAC receiver / guarded
SIMULATION pipeline. It never starts `get_engine()`, `CCXTBinanceBridge`,
`QuantExecutionBridge`, the legacy streamer or macro/world integrations.
All other HTTP routes (even health, OpenAPI, mode changes and world/quant
mutations) remain 404. Only
`GET /api/v1/market/verified/status` is exposed; that response includes
RUNNING / COMPLETE_BOUNDED_RESEARCH / FAILED, never a silently substituted
legacy feed. A stopped or failed finite task is not automatically restarted.

### Manual local preview — not production

```powershell
Set-Location F:\econith
$env:ECONITH_SPOT_INGRESS_POLICY = "verified_forward_preview"
$env:ECONITH_SPOT_FORWARD_PREVIEW_CONSENT = "PUBLIC_MARKET_DATA_RESEARCH_ONLY"
$env:ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR = "$env:TEMP\econith-forward-preview-unique-new-01"
python -m uvicorn main:app --host 127.0.0.1 --port 8776
```

From a second PowerShell terminal, while the app is running:

```powershell
Invoke-RestMethod "http://127.0.0.1:8776/api/v1/market/verified/status" |
  ConvertTo-Json -Depth 5
```

Choose a **new** evidence directory per attempt and stop the preview
server when the finite research session finishes. This command uses only
public Binance TLS/WSS/REST market-data endpoints and does not require API
keys. If the public clock, DNS, peer TLS or snapshot fails, the run fails
closed. It does not backfill the outage or guarantee full market coverage.

## Proven and still open

CI on commit `4bd586fba83a2351d43f29d79d18abdb5332d97e`:
[Python CI #39100349644](https://github.com/coffee2307/econith/actions/runs/39100349644)
**707 passed, 1 skipped**;
[images #39100349643](https://github.com/coffee2307/econith/actions/runs/39100349643),
[container smoke #39100349661](https://github.com/coffee2307/econith/actions/runs/39100349661)
SUCCESS. Tests include crash before any STAGE, later missing STAGE,
conflicting staged payload, tampered original, prevention of retroactive
book lookup, app preflight denial, legacy HTTP isolation and complete
bounded status. They use **offline source fixtures** and genuine local
receiver subprocess/SQLite; they are not a fresh multi-day real-network
soak at this code revision.

**Still required for full Phase 1 institutional acceptance:** genuinely
continuous long-running protected source (not finite preview); bounded
reconnection and measured outage-window accounting; sustained throughput,
latency/backlog/disk tests under public market conditions; reviewed
**default** `main.py` cutover with independently audited publisher and
subscriber inventory; multi-symbol/OHLCV and any applicable derivatives
and macro release-vintage coverage; external operator 24–72h acceptance.
Maintain Draft PR #28 and never enable live orders from this evidence.
