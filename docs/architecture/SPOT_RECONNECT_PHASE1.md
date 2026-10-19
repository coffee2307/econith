# ECONITH Phase 1 — offline Spot reconnect controller and outage ledger

**Scope:** this is opt-in SIMULATION-only research. There is no automatic
startup wiring, no orders, no API credentials, no live capital, no production
deployment and no EventBus acknowledgement. The reconnect controller uses the
already isolated public market data capture adapter in
`infrastructure/spot_public_capture_adapter.py`.

## Durable state and identity

`infrastructure/spot_reconnect_controller.py` implements
`OfflineSpotReconnectController(journal, controller_id=...,symbol=...)`.

All state is stored in the SAME SQLite database as the immutable original WS
frames / REST depth snapshots, using WAL FULL-synchronous transactions:

- `offline_spot_reconnect_controllers` permanently binds controller ID to
  canonical Spot symbol; do not mix BTC and ETH sessions under one identifier.
- `offline_spot_reconnect_attempts` records each unique session ID, attempt
  sequence number, started/finished receipt clocks, outcome
  `STARTED/SUCCESS/FAILED/INTERRUPTED`, sanitized exception type, snapshot
  book lastUpdateId and captured WS frame count. A new attempt is recorded
  **before** calling any external capture callback.
- `offline_spot_reconnect_outages` records failed-session intervals,
  failed attempt counts, last bounded failure code and first successful later
  session/time that restored an **observable current book**. Outage history
  always keeps `coverage_verified=0`.

`run_batch(capture_once, max_attempts<=10, backoff_s<=30)` retries for a
bounded number of sessions, with an exponentially increasing but capped
backoff between attempts. A failed attempt produces a durable outage;
a later healthy capture can mark it **restored**, never retroactively
**complete**. Each capture gets a distinct random `session_id`, new
WebSocket scope and its own REST snapshot. A bounded run returns
`RECOVERED` or `EXHAUSTED`, with
`history_continuity_verified=False` in **all** cases. There is no
automatic infinite retry or cross-session interpolation.

Before marking an attempt SUCCESS, the controller independently reloads its
original bytes from the raw capture journal with
`OfflineSpotCaptureReplayer`, requires a synchronized depth book, checks
source session closure and result provenance, and checks that the source
session and receipt clock belong to the current attempt. It does not trust
`SpotTransportResult` as an exchange signature or as authoritative
testimony from an arbitrary callback.

## Restart and fault policy

A process crash can leave `STARTED` in SQLite. The next invocation of
`run_batch` **does not automatically steal this attempt**, because the
original worker might still be alive. It fails closed instead.

After an operator or outer orchestration layer has independently established
that the old worker is no longer running, call
`reconcile_crashed_attempts()`; this converts unfinished attempts to
`INTERRUPTED` and opens/extends a durable outage transactionally. Repeating
the reconciliation is idempotent. **Do not call it while another capture
worker is active**: the current controller does not yet implement a durable
cross-process leader-lease/heartbeat and cannot prove process death.
This is an explicit residual concurrency restriction, not a safety proof.

If the SQLite transaction that should store a FAILED attempt and its outage
fails (e.g. a disk-full error), both changes roll back. The attempt may
remain STARTED and requires explicit crash reconciliation; do not claim the
outage was recorded or the session recovered. When switching to REALITY,
new capture/ledger reads or writes are blocked, leaving any unfinished
attempt for later SIMULATION-only reconciliation.

Only the exception **class name** is retained as a failure code; messages,
connection URLs, request query strings and possible credentials must not
be written to the outage ledger. The full source-body journal is still
limited to public market data payloads.

## Opt-in public-data-only usage

```python
import asyncio
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController

async def main():
    journal = OfflineSpotCaptureJournal(
        "research/spot-originals.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    adapter = OptInSpotIngressAdapter(journal, symbol="BTCUSDT")
    controller = OfflineSpotReconnectController(
        journal, controller_id="btc_public_capture", symbol="BTCUSDT",
    )
    # Explicit opt-in: public WSS/HTTPS only, GET snapshot, no trade route.
    outcome = await controller.run_public(
        adapter, allow_network=True,
        max_attempts=3, backoff_s=1.0,
        max_frames=300, timeout_s=30,
    )
    print(outcome)
    print(controller.outages())

asyncio.run(main())
```

All tests use injected sockets/REST responders. The example is NOT part of
application startup and has not been run against live Binance in this PR.

## Test acceptance and Phase 1 gates

`tests/test_spot_reconnect_controller.py` tests failed then restored
sessions, separate authenticated **caller-scoped** identities, bounded
backoff, durable exhaustion across restart, explicitly reconciled crashed
attempts, concurrent-runner refusal (no auto-steal), SQLite rollback,
cancellation, mode transitions, faked SUCCESS, recycled old capture,
cross-symbol reuse and non-continuity across two healthy finite sessions.

Limitations preventing Phase 2 sign-off:

1. No genuine captured live Binance evidence or measured wall-clock
   uncertainty; TLS hostname verification is a transport control, not proof
   of complete exchange order flow.
2. No durable cross-process leader lease or worker fencing yet; crash
   reconciliation requires proof that the old worker is dead.
3. Controller retries are bounded **per call**; no global circuit-breaker,
   durable daily retry budget or dead-letter queue.
4. An outage recovery restores a *current book* but cannot retroactively
   prove that missed `aggTrade` or `depthUpdate` frames were delivered.
5. No verified-only default ingress cutover, cross-process consumer ACK,
   telemetry alerts for backlog or real end-to-end disconnect soak.

Green CI is necessary to accept the code on the draft research branch;
it is not proof of profit, market completeness, or permission to trade.
