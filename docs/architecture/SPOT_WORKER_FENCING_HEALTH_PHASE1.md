# Phase 1 — durable fenced reconnect ownership and read-only ingress health

**Research-only opt-in.** No exchange orders, credentials, production startup
hooks, live funds, external broker ACK or cross-service exactly-once guarantee.
This contract extends the isolated Spot capture/reconnect workflow on Draft
PR #28; it does not authorize switching the default data pipeline.

## Durable exclusive worker lease

`infrastructure/spot_worker_fence.py` implements
`OfflineSpotWorkerFence(journal, controller_id, clock_ms, lease_ms=30000)`
in the **same SQLite database** as the immutable source frames, reconnect
attempts and outages.

- `acquire(holder_id)` uses `BEGIN IMMEDIATE` to atomically reject another
  active owner. An expired owner can be superseded by a **new generation**
  and cryptographically random 48-character token. Lease history survives
  restarts; generations increase rather than reset.
- `renew(claim)` checks controller ID, holder, generation, token, expiration
  and local clock in the transaction; it returns an updated claim containing
  the new expiration. Old claims (even from the same token and generation)
  cannot renew or finalize after a heartbeat.
- `release(claim)` only expires the latest still-current claim. Stale holders
  cannot release someone else's new lease.
- `status(now_ms)` exposes holder/generation/active flag but **not the token**.

The controller's persisted `requires_fence` policy is one-way: constructing
`OfflineSpotReconnectController(..., worker_fence=fence)` enables the
requirement for that controller ID. Future controller instances that omit the
fence are rejected; even an older in-memory controller object must pass a
current claim before it can mutate that controller. The policy is checked
**inside** the same SQLite `BEGIN IMMEDIATE` transaction as `begin_attempt`,
`reconcile_crashed_attempts` and terminal SUCCESS/FAILED/INTERRUPTED updates.

`run_fenced_batch(capture_once, holder_id, ...)` acquires the owner lease,
then reconciles abandoned STARTED attempts as INTERRUPTED under the *new
generation* and executes the bounded retry loop. The controller renews the
lease periodically during long asynchronous captures. A worker whose token
expires or is replaced cannot write attempt results or close/open outage
rows. `run_fenced_public(..., allow_network=True)` opts into the existing
read-only Binance public Spot WSS/GET snapshot adapter; neither method is
wired into application startup.

**Important limitations:** A SQLite lease fences the **controller's database
state transitions only**. It cannot forcibly close a previously opened
WebSocket in another process, prevent an uncooperative former worker from
appending its separate raw-capture session, or ensure that a network side
effect executes exactly once. Host clocks may disagree; lease safety assumes
an adequately bounded, monotonic wall clock. A legitimate long-running
worker can lose ownership after a prolonged stop-the-world pause; it must
stop on renewal failure. Never use this mechanism as authorization for live
order placement or financial transactions.

The legacy `run_batch` is retained for existing offline experiments with
unfenced controllers; it is not safe multi-process coordination. Once the
`requires_fence` policy is activated, unfenced begin/commit/reconcile is
refused even via the legacy method. Use different controller IDs if an old
offline test must continue in legacy mode.

## Read-only health snapshot and backlog

`infrastructure/spot_ingress_health.py` implements
`OfflineSpotIngressHealthMonitor(controller,worker_fence=...)`.
On `snapshot(now_ms=..., max_recent_capture_age_ms=...,
max_outage_age_ms=..., source_consumer=..., receiver=...,
max_pending_projections=...)`, it reports:

- Number of raw-capture sessions, original frames and stored raw-byte volume
  for the symbol. These are **locally recorded** counts, not exchange-wide
  market completeness or throughput.
- Reconnect attempt counts by terminal/unfinished status, most recent
  verified capture commit time, open outage count and open outage age.
- Durable worker lease active state and generation if configured; no secret
  owner token leaks into the report.
- The count of **unacknowledged local projections** for a *specific*
  `(source_consumer,receiver)` pair, if both durable projection and inbox
  receipt tables exist. All eligible projected ingest IDs are counted
  against that receiver's receipt rows in SQLite. No producer/receiver
  registration means backlog is `None`, not zero.

Findings use explicit flags: `NO_VERIFIED_CAPTURE`,
`LAST_VERIFIED_CAPTURE_STALE`, `OPEN_OUTAGE`,
`OUTAGE_AGE_THRESHOLD`, `UNFINISHED_CAPTURE_ATTEMPT`,
`NO_ACTIVE_FENCED_WORKER`, `PROJECTION_BACKLOG_THRESHOLD` and
`PROJECTION_BACKLOG_UNKNOWN`. **They are computed on demand only**:
no scheduled alerting, dashboards, push notifications, remote observers or
autonomous monitoring have been configured.

## Offline operator pattern

```python
import asyncio
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter
from infrastructure.spot_worker_fence import OfflineSpotWorkerFence
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController
from infrastructure.spot_ingress_health import OfflineSpotIngressHealthMonitor

journal = OfflineSpotCaptureJournal(
    "research/spot-originals.sqlite",
    mode_provider=lambda: QuantMode.SIMULATION,
)
fence = OfflineSpotWorkerFence(
    journal, controller_id="btc_public", lease_ms=30000,
)
controller = OfflineSpotReconnectController(
    journal, controller_id="btc_public",
    symbol="BTCUSDT", worker_fence=fence,
)
monitor = OfflineSpotIngressHealthMonitor(
    controller, worker_fence=fence,
)
print(monitor.snapshot())  # read-only, no sockets
# Real public market-data-only capture requires EXPLICIT authorization:
# adapter = OptInSpotIngressAdapter(journal, symbol="BTCUSDT")
# result = asyncio.run(controller.run_fenced_public(
#     adapter, holder_id="local_reader_001", allow_network=True,
# ))
```

If a former worker may still have a running socket, a new lease cannot
prevent it from attempting network capture; treat all affected session data
as individually scoped research evidence and verify before downstream
consumption. Outage history never certifies missing frames as recovered.

## Fault-injection acceptance

`tests/test_spot_worker_fence.py` exercises SQLite contention, sequential
generations, stale-token fencing, heartbeat renewal, expiry/takeover,
concurrent controllers, recovery of abandoned attempts under new ownership,
legacy bypass refusal, mode denial and validation. The existing reconnect
tests remain valid for the explicitly *legacy* offline mode.

`tests/test_spot_ingress_health.py` exercises zero-data UNKNOWN semantics,
worker abandonment, open outage age, verified capture counts and staleness,
per-receiver inbox backlog thresholds and REALITY-mode denial.

**Phase 1 exit blockers remain:** genuinely recorded public Binance TLS data
and reliable receive clocks, end-to-end fault-injection across process
boundaries, cross-process receiver-owned durable ACK and backlog metrics
using actual traffic, and audited verified-only default ingress cutover.
Green offline tests alone cannot establish market completeness, alpha,
production readiness, or financial permission.
