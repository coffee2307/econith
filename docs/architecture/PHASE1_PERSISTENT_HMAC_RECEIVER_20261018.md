# Phase 1 — persistent local HMAC receiver for bounded forward research

**Scope:** opt-in read-only SIMULATION, one independently owned Python
receiver subprocess **per finite public Spot source session**. This is not an
always-on service, no remote broker, no default data-plane cutover, no
uninterrupted Binance trading history and no authorization for exchange
orders, account use or live capital.

## Motivation and implementation

Until this iteration, `spot_forward_verified_research.py` called
`accept_batch_via_authenticated_subprocess` for EACH verified source event
after COMMITting its immutable producer outbox STAGED entry. That starts
an entirely fresh Python interpreter for every event, which is an obvious
throughput/latency bottleneck and creates excessive process churn.
Previous performance could not credibly meet continuous market-data
throughput.

`infrastructure/market_persistent_hmac_receiver.py` introduces
`PersistentHmacResearchReceiver` with a single OS child per finite
research session. The worker lives at
`ci/phase1_persistent_receiver_worker.py`. It is a **private local
pipe protocol**, never an HTTP or socket service.

For each bounded request (up to 32 typed envelopes and 1MB wire JSON):

1. Original public WS frame or REST snapshot is COMMITted in the
   source-owned SQLite; the source shadow validates exact immutable
   bytes/time and source-specific as-of order book.
2. The parent COMMITs the canonical exact `ProjectionEnvelope` as
   `STAGED` in its own WAL/FULL outbox.
3. A signed v1 persistent request includes sequence number, immutable
   envelopes and receipt timestamp. The HMAC key is an ephemeral
   256-bit per-child secret passed via the child environment (not command
   arguments or logs). Every request requires the strictly next sequence.
4. The independent subprocess verifies HMAC and sequence **before**
   writing the receiver-owned SQLite ledger. It individually COMMITs each
   envelope, with exact same-source retries idempotent.
5. The signed response contains the SAME sequence and exact durable
   receipt metadata. The parent checks signature, source coordinates,
   fingerprint, timestamp bounds and receiver-owned receipt ID before
   advancing `RECEIVER_ACKED`. Only then are guarded read-only
   MarketDataPipeline and three separately durable observers allowed.
6. After their ACK checks, producer advances to `DERIVED_ACKED`. The
   session-level original/outbox/receiver/derived ledgers are reopened
   and independently compared before research acceptance.

The parent serializes all requests with an async lock, times out each
pipe send/receive and never guesses a receipt from process stdout or
absence of an exception. On worker exit, invalid MAC, wrong sequence,
broken pipe or timeout, it marks the client poisoned and kills the child.
If the receiver COMMITted just before dying, the producer outbox remains
STAGED/RECEIVER_ACKED and must be reconciled by the existing exact
source-original recovery; a new child never implies a new source session
is continuous with the old one.

Limits: up to 4,096 ordered requests per worker, bounded 1MB per
wire frame and 32 envelopes per request; default IPC ACK timeout 10s
(configurable 1–30s). The forward source adapter retains its existing
bounded queue and fail-closed session-timeout policy. The HMAC secret
authenticates cooperating trusted processes on one host; it does not
protect against a compromised host or supply TLS/remote-broker security.

## Manual operator opt-in

Use a **fresh absolute output directory** on an authorized checkout.
This command uses Binance public Spot TLS/WSS/REST GET data and a fresh
clock preflight before each finite attempt; no exchange account keys.

```powershell
Set-Location F:\econith
python -m ci.phase1_forward_endurance_trial `
  --allow-public-market-data `
  --persistent-receiver `
  --output-dir "$env:TEMP\econith-persistent-trial-unique-01" `
  --attempts 3 --max-frames 160 `
  --timeout-s 45 --max-storage-mb 100
```

The `--persistent-receiver` flag is opt-in. Without it the reviewed
one-shot subprocess transport remains available as a control path.
Neither flag modifies `main.py`'s default legacy startup or enables
execution. The trial is intentionally finite, and every reconnect has
a brand-new source session / REST snapshot and recorded outage history.

Metrics in `forward_supervisor.sqlite.forward_watch_attempt`:
`receiver_process_starts` (persistent expected **1 per successful
finite source session** vs one-shot equal to admitted events),
`max_receiver_ack_latency_ms` (measured elapsed process/pipe/ACK time),
source `queue_high_watermark`, original counts, receiver receipt counts,
recorded disk footprint and sanitized failure code. These are
observable measurements, NOT an invented throughput SLA or a full
statistical benchmark. Outage `coverage_verified` remains **0**.

## Test evidence / full Phase 1 blockers

CI tests execute actual OS receiver subprocesses and SQLite WAL/FULL:
- one PID durably accepting multiple source envelopes and a bounded batch;
- exact-same-envelope retry idempotence;
- wrong shared HMAC key, repeated/incorrect sequence and incomplete
  signed ACK rejection;
- forced kill of a real receiver process and no invented ACK;
- persisted receiver COMMIT followed by producer ACK failure, recovered
  via original-byte outbox replay;
- full forward source session with a single persistent PID, primary
  and both named independent derived observers, and measured ACK latency;
- two independent supervised source sessions, one receiver process per
  session and genuine queue/disk/receipt proof.

**Gaps remaining for institutional Phase 1 exit:** persistent IPC
does not create a genuinely unbounded verified continuous WS source,
does not fix all missed events on reconnect, and has no real-network
24–72h throughput, tail-latency, RAM, high-volume backpressure or host
power-loss acceptance for this commit. Original multi-symbol/OHLCV,
optional derivatives and macro point-in-time coverage and reviewed
legacy default cutover remain OPEN. PR #28 must stay Draft until those
gates are reviewed.
