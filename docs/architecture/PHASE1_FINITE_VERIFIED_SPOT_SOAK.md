# Phase 1 — supervised, finite, verified-only Spot research soak

**Status:** research safety evidence, NOT continuous live market integration,
not production readiness and NOT authorization for real exchange orders.

## Why a separate supervisor

Binance public Spot capture already has a bounded FIFO writer, durable original
bytes, no silent overload loss, independent post-COMMIT incremental validation,
session-specific REST snapshot/diff synchronization, explicit reconnect/outage
ledger and HMAC-authenticated local-process source/receiver receipt path.
It is wrong to bypass those safeguards to show a lower-latency chart.

`infrastructure/spot_verified_soak.py` and the manually invoked
`ci/phase1_verified_spot_soak.py` supervise 1–24 distinct, individually
sealed `observe_verified_public_batches(..., cycles=1)` sessions. The original
capture code remains unchanged. Each round's first receipt clock and publisher
rights belong to that round only: rolling indicator state never crosses the
unobserved gap. A freshly synchronized full book is used only after the
corresponding capture is committed and the full receiver ACK proof passes.

For *actual* public TLS transport (not injected fixture capture), the CLI
requires `--allow-public-market-data` and calls the public Binance time
endpoint before **each** round, rejecting implausible RTT or clock offset.
This is only a coarse clock plausibility test, not synchronized signed exchange
time. All network activity is public market data; no API keys, private REST,
CCXT bridge, order intent or position control are present.

## Manual example — Windows PowerShell

Run on an isolated branch/review environment, not production and not
from `main.py`. Use an existing Python 3.12 environment with locked
repository dependencies. The output directory MUST NOT already exist.

```powershell
Set-Location F:\econith
python -m ci.phase1_verified_spot_soak `
  --allow-public-market-data `
  --output-dir "$env:TEMP\econith-phase1-soak-20261018" `
  --rounds 4 --max-frames 160 `
  --timeout-s 45 --max-attempts 2 --pause-s 2
```

**Caution:** use a fresh output name for each new test. The sample name
is illustrative; do not overwrite previous experiment results.

To continue a cleanly checkpointed soak that was stopped *between* rounds,
pass **the identical flags** plus `--resume`. This is permitted only when
the manifest is RUNNING and no uncheckpointed directory exists. If the
connection, source, database, consumer, process or report fails *during*
a round, never overwrite that partial evidence: the supervisor refuses
resume into the same root and an operator must inspect/quarantine it and
start a new root. `--resume` is not crash recovery of an unknown in-flight
transport session and never claims gap-free data.

## Failure model and durable checkpoints

Each round is validated for:

1. Exactly one closed, unique source session with synchronized Spot order
   book and verified original trade count.
2. Independent source projection ACK count == receiver-owned durable receipt
   count == trades, with authenticated local subprocess response.
3. Each of the three research-derived topics has exactly its expected
   receipts; two independently named durable consumers `research_audit` and
   `research_monitor` match total receipts.
4. Original `original_public.sqlite`, sealed `microbatch_progress.json`
   and `cycle_01/chain_acceptance.json` are present and consistent.
5. Manifest flags explicitly state `continuous_live_market=false`,
   `history_continuity_verified=false`, and `orders_submitted=0`.

Only a fully verified round is appended to
`verified_soak_manifest.json`. The JSON is atomically replaced after a
file flush/fsync. The manifest stores canonical SHA-256 digests of two
immutable reports for each round. The hashes **detect accidental local
changes**, not tampering by an adversary able to rewrite both manifest and
artifacts. Reopening a checkpoint rechecks every committed round's hashes,
source IDs and scope, and rejects foreign, partial or uncommitted directories.
If interruption occurs while writing a `.pending` manifest, recover manually:
no automated inference of successful producer or subscriber ACK is permitted.
An exclusive `.verified_soak.lock` prevents concurrent operators or two
resume processes from writing the same manifest. Normal exits release the lock;
a hard OS process kill leaves an orphan lock, intentionally requiring manual
inspection of the prior capture/checkpoint before any new run. Never delete
an orphan lock while another supervisor is operating on that root.

## Coverage and open Phase 1 gates

Passing 24 finite rounds is a **bounded research soak**, not a 24-hour run,
not a 24/7 verified feed and not production availability. Between rounds,
capture is intentionally stopped; unobserved trades cannot be synthesized
or labeled as complete history. Report `public_tls_peer_confirmed=false`
for injected fixture transports. Static CI uses **no public network**;
an operator must separately run fresh real public Binance source observations
if an external-transport acceptance claim is needed.

Open gates remain:
- truly continuous streaming/verified processing and consumer durability
  under long-running backpressure, with recovered source session/book resets;
- correctness of event-time/as-of derived orderbook snapshots throughout a
  continuous session (never backdate the final snapshot);
- reviewed `main.py` verified-only default cutover, removal of legacy raw
  bypasses, exhaustive publisher/subscriber inventory and isolation of all
  execution/REALITY routes;
- broad original Phase 1 data types (OHLCV coverage, multi-asset symbols,
  optional funding/open interest and macro release vintage) and a reviewable
  data-quality acceptance matrix.

Keep PR #28 Draft; no merge, production deploy or real orders.
