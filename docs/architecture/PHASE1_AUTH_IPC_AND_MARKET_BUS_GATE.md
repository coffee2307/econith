# Phase 1 final trust-boundary hardening — local HMAC IPC and raw EventBus gate

**Status:** Draft PR #28, optional SIMULATION-only research path. These
safeguards do **not** automatically connect to production/live markets or
replace the default legacy application pipeline.

## One-shot receiver-owned ACK in a separate authenticated OS process

`ci/phase1_authenticated_receiver_ipc.py` provides
`accept_via_authenticated_subprocess(envelope, receiver_db, now_ms)`.

- Creates an ephemeral random 256-bit HMAC secret for each local subprocess
  transaction and gives it to the child through its environment, not command
  arguments or logs. This requires a trustworthy single machine/operator.
- Encodes the exact canonical source projection and receipt-time as bounded
  UTF-8 JSON (at most 1 MiB). Both client->worker envelope and worker->client
  receiver-owned ACK are individually authenticated with HMAC-SHA256 and
  **direction-separated** domains. Comparison uses constant-time digest
  checking. No `pickle`, dynamic deserialization or network listeners.
- The child must be invoked with a private `--child` mode and HMAC key. Its
  only side effect is the distinct SQLite receiver-owned receipt transaction.
  The producer ACK is written in a separate SQLite DB only after a verified
  child ACK returned; a process crash after receiver commit remains safely
  idempotent across replays.
- Duplicate attempts with matching canonical source IDs return the original
  durable receipt; conflicting replay fails closed; invalid/mismatched MACs
  do not produce an ACK. The child does not expose raw messages, keys,
  private paths, or exception text.
- This proves **authenticated, local subprocess IPC**, not remote TLS,
  distributed-broker guarantees, multi-host identity, authenticated exchange
  timestamps, or exactly-once external financial side effects.

`ci/phase1_offline_real_capture_chain.py` now supports the explicit
`--authenticated-subprocess` switch in addition to its existing in-process
fixture mode. On this switch, each of the original verified aggregate trades
is dispatched to an independent local Python process before producer ACK.

### Operator command on the previously saved genuine public Spot evidence

Run **after** the exact PR HEAD is green in Python CI, using an isolated
*new* output directory and the same saved read-only original public capture:

```powershell
$work = Join-Path $env:TEMP "econith-p1-20261017-225926"
$py = Join-Path $work ".venv\Scripts\python.exe"
$repo = "F:\econith"
$branch = "chore/phase0-inventory-hygiene-20261017"

git -C $repo fetch origin $branch
git -C $work switch --detach "origin/$branch"
Set-Location $work

$original = Join-Path $env:TEMP "econith-p1-evidence-v2-20261017-232246\original_binance_spot_public.sqlite"
$output = Join-Path $env:TEMP ("econith-p1-auth-ipc-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

& $py -m ci.phase1_offline_real_capture_chain `
  --source-db $original `
  --output-dir $output `
  --session-id "rec_7544b3d0c2dbad0e44e86dcf" `
  --authenticated-subprocess

Write-Host "EXIT CODE:" $LASTEXITCODE
if (Test-Path (Join-Path $output "chain_acceptance.json")) {
    Get-Content (Join-Path $output "chain_acceptance.json") -Raw
}
```

Expected: `PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK`,
`original_records_replayed=501`, `original_trades_replayed=184`,
`lineage_manifest_entries=184`, `producer_ack_count=184`,
`independent_receiver_receipt_count=184`,
`transport_scope=HMAC_AUTHENTICATED_LOCAL_SUBPROCESS_PIPE_ONLY`,
`cross_process_authenticated_transport=true`, `network_requests=0`,
`orders_submitted=0`. A successful run proves exactly the local
subprocess boundary, no remote broker link or ongoing uninterrupted data.

## EventBus raw source bypass protection

`core/event_bus.py` adds the optional
`EventBus.activate_verified_market_only()` capability-based pre-start gate.

- **Unchanged by default.** A new regular EventBus still accepts the two
  legacy `md.aggTrade` / `md.depth` topics. All existing startup paths,
  mock market simulation and derived `md.ticker` continue as before.
- In a separate, explicitly configured `QuantMode.SIMULATION` research
  EventBus, the gate must activate **before** any queued events or before
  startup. Once active, public direct `publish(...)` to raw market topics
  fails closed even if the payload claims `source=verified`.
- Only the first non-forgeable per-bus in-process capability can publish
  the protected raw topics through `VerifiedMarketPublisher.publish_raw`.
  Forged capabilities, `QuantMode.REALITY` and unrelated topics are denied.
- **This is a publisher identity gate, not data verification.** Whoever
  owns the capability must still do original-byte replay, source checks
  and receiver receipt validation before publishing. It does not stop a
  malicious same-process Python module that can bypass private objects,
  nor does it guard arbitrary future market topics not yet inventoried.

Crucially, the default `main.py` *still* wires the partial `depth20`
`BinanceWebSocketStreamer`. The existing
`ECONITH_SPOT_INGRESS_POLICY=verified_only` startup preflight still
**refuses to boot**, because it would otherwise claim verified source
coverage while bypass paths exist. Do not turn this flag on or declare
a production cutover complete simply because the optional EventBus gate
exists.

## Remaining mandatory sign-offs

- On the real 501-record public Spot capture: exercise the new
  `--authenticated-subprocess` runner and inspect report/exit code.
- Test multi-session live read-only reconnect/forced disconnect and quantify
  unrecoverable intervals, not just successful isolated sessions.
- Audit all raw and derived market publishers across the full application,
  then implement a reviewed cutover that exclusively consumes verified
  sources and blocks the old partial-depth/default EventBus bypass. This
  must explicitly define the semantics of offline-research vs real-time
  runtime before switching defaults.

No approval is implied for merge, live execution, strategy profitability
or production deployment. Phase 1 cannot be formally called 100% until its
declared end-to-end runtime admission gates are demonstrably satisfied.
