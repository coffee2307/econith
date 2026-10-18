# Phase 1 — Pinned public Spot single-socket segment reuse + startup-boundary audit

**Status:** engineering research, opt-in public market data, SIMULATION, no
exchange orders/keys and no production cutover. PR #28 is Draft.

## Why this is not another finite-session supervisor

The prior supervised operator watch independently opened a new public TLS
WebSocket for every bounded forward capture session. This generated an
avoidable network reconnect/snapshot cycle even when the socket was healthy,
and an unknowable exchange-data gap at every handoff. One-shot opens could
not be treated as an uninterrupted authenticated source.

The new `infrastructure/spot_public_pinned_lease.py` establishes exactly
one pinned Binance public Spot `wss://data-stream.binance.vision/stream`
WebSocket (default TLS peer verification, keepalive, max 16 pending frames,
bounded payload bytes), plus a read-only `https://data-api.binance.vision`
REST snapshot client (TLS peer verification, no redirect, no environment
proxy, no account keys). It reuses those network transports across several
**independently certified** bounded session segments; each segment still
creates a fresh source-owned immutable SQLite, a new independently
available REST order-book snapshot, post-COMMIT as-of shadow validation,
outbox, persistent HMAC receiver process, independent derived observers,
and per-event hashed live checkpoints.

`infrastructure/spot_single_socket_watch.py` owns the leased source
and invokes the existing `run_forward_research_supervisor` policy gates,
clock check **before every segment**, durable root ownership, session
attempt/outage ledger and disk/frame/time budgets. Its per-segment callback
calls `observe_forward_spot_research(...,public_lease=...)` without
injecting or claiming a fixture-only source identity. The lease is owned
only by the trusted public connector (fixture tests are explicitly labeled
**not TLS-attested**). On ANY segment failure, the lease is invalidated and
closed before the next attempt; the next segment may open a new verified
public WSS and MUST establish a fresh REST book. The original failed
session remains quarantined.

The operator result includes `public_socket_starts`,
`successful_socket_reuses`, `discarded_broken_sockets` and a Boolean
indicating the transport originated in the real trusted TLS factory
versus test injection. Even multiple successful segments on one socket
**cannot** certify exchange history completeness: segment boundaries are
still recorded as `coverage_verified=0` in
`forward_watch_unobserved_boundary`. The previous outage table and
lossless-within-known-original-prefix source policy remain unchanged.

### Manual operator command (public data only)

In an authorized, reviewed repository checkout, with a truly new absolute
directory and no API keys:

```powershell
Set-Location F:\econith
python -m ci.phase1_forward_endurance_trial `
  --allow-public-market-data --persistent-receiver --single-socket `
  --output-dir "$env:TEMP\econith-single-socket-new-01" `
  --attempts 3 --max-frames 160 --timeout-s 45 `
  --ws-idle-timeout-s 15 --max-storage-mb 100
```

A longer operator-driven trial can additionally select
`--watch-hours 24 --attempts 2048` but **has not been executed or
verified** by this code change. The process cannot be called a 24-hour
observation unless the root-owned watch plan records an achieved time
target, all intended receipt chains pass, outages are classified and
resource use is measured. The CLI returns nonzero on any failed source
or underachieved operator time target. It opens only read-only public
Binance market-data endpoints.

## Additional task 1 — real failure/reconnect integration tests

`tests/test_spot_single_socket_watch.py` uses an injected iterator
that simulates ONE network socket across two successive segments. Each
segment must persist genuine source SQLite, build a separate REST book,
get HMAC ACKs from a real independent Python receiver process, commit
three independently durable research observers and complete its
checkpoint hash chain. The test verifies **one socket start**, two
accepted independently sealed sessions and two REST snapshots, with
unknown session boundary. An injected network disconnect in segment
two must discard the socket; segment three starts a **new** leased
connection and fresh REST book. Absent opt-in and a non-lease object
are denied. All test-injected sockets are correctly flagged as not
evidence of public TLS peer identity.

## Additional task 2 — fail-closed legacy-cutover inventory in CI

`ci/phase1_default_source_boundary_audit.py` parses actual
`main.py` and `spot_verified_cutover_gate.py` with Python AST. The
existing protected `verified_forward_preview` and `verified_only`
branches must be entered in the reviewed order **after** the initial
policy preflight, set the protected HTTP research-only marker and
return before the legacy startup path. Engine singleton, legacy WS
publisher, CCXT bridge or effectful execution providers cannot be
called in either research branch. The guard also requires the
explicitly unchanged **legacy** default and its residual legacy
publisher/engine/bridge inventory. A default-source change fails the
audit and requires independent manual review rather than quietly
claiming an institutional verified cutover.

`.github/workflows/ci.yml` invokes this audit before pytest; tests
inject forbidden engine calls, delete a required early return and
alter the default policy to prove that CI refuses unreviewed changes.

**Residual exposures:** The actual default application is STILL
`legacy`, constructs `BinanceWebSocketStreamer` and can publish
unverified market depth. The static audit is an institutional change
guard, **not** a dynamic runtime proof that all legacy sources are
isolated in an otherwise production-ready app.

## Institutional exit obligations

This feature permits multiple **source-verified bounded segments on a
shared socket** and source re-admission after a broken link, rather than
claiming complete single-socket uninterrupted exchange history.
A network frame/venue outage within TCP/WSS cannot be reconstructed
without a separately verified historical archive and cross-market
reconciliation. Required for Phase 1 final 100%:
- Sustained live public Spot single-source observations with actual
  24–72h independent host evidence, CPU/RAM/backpressure/throughput/
  order-book integrity, power-loss restart and outage accounting.
- Reviewed default market-source migration and effectful subscriber
  cutover; no legacy publisher bypass.
- Broader multi-symbol/OHLCV and applicable derivative/macro
  point-in-time datasets and retention/quality acceptance.

No merge, production deploy or real-money trading is authorized.
