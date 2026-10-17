# Phase 1 — Operator evidence: bounded live Spot capture, offline 3-DB replay

On 2026-10-17 the operator supplied a local console transcript for a **public,
read-only** BTCUSDT market capture from Binance at exact draft HEAD
`f09e84c4c67b48be2de08becd3d25fa58c2982ba`.

- Local selected regression suite: **60 passed**.
- Acceptance: `PASSED_OBSERVED_BOUNDED_SESSION`, exit 0,
  `RECOVERED` on attempt 1; source session was closed.
- 500 WebSocket messages (184 `aggTrade`, 316 `depthUpdate`) plus one REST
  snapshot persisted; 501 original-record replay; `SYNCED` book; final
  book update ID 101566335645.
- Coarse Binance public-time check: offset +439 ms, measured round trip 922
  ms, estimated symmetric one-way uncertainty 461 ms.
- No outage was recorded **during that bounded observation window**. The
  ledger explicitly says `history_continuity_verified=false`.
- This is **operator-provided evidence**, NOT yet an assistant-inspected raw
  SQLite file or a certified, gap-free exchange history.

## Offline independent verification using retained evidence

`ci/phase1_offline_real_capture_chain.py` reads the saved source SQLite
through `mode=ro`, creates a fresh SQLite backup in a separate research
output directory, replays all captured bytes and reconstructs a synced
book, promotes original aggregate trades to a **new** verified market
SQLite journal, writes source-to-market lineage manifest, projects
research-only data and stores idempotent receipts in an independent receiver
SQLite file.

The original source is never opened for writing. The command has **zero
network requests**, no API keys, no strategy execution and no orders.
Projection contents contain only observed price/quantity and source ID.
This is still local same-process dispatch to a separate receiver database;
it does NOT authenticate remote IPC or establish external exactly-once.

Example PowerShell using the previously created detached worktree:

```powershell
$work = Join-Path $env:TEMP "econith-p1-20261017-225926"
$py = Join-Path $work ".venv\Scripts\python.exe"

# Fetch and check out latest reviewed PR commit in detached worktree.
git -C "F:\econith" fetch origin chore/phase0-inventory-hygiene-20261017
git -C $work switch --detach "origin/chore/phase0-inventory-hygiene-20261017"
Set-Location $work

$inputDb = Join-Path $env:TEMP "econith-p1-evidence-v2-20261017-232246\original_binance_spot_public.sqlite"
$evidenceOut = Join-Path $env:TEMP ("econith-p1-chain-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
& $py -m ci.phase1_offline_real_capture_chain `
  --source-db $inputDb `
  --output-dir $evidenceOut `
  --session-id rec_7544b3d0c2dbad0e44e86dcf

Write-Host "EXIT CODE:" $LASTEXITCODE
Get-Content (Join-Path $evidenceOut "chain_acceptance.json") -Raw
```

Successful output requires `PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK`,
`original_records_replayed=501`, `original_trades_replayed=184`,
`lineage_manifest_entries=184`, and **matching 184 producer ACKs and 184
receiver-owned receipts**. If source data differ, fail closed; don't forge or
patch snapshots/trades. Keep the raw SQLite database local unless separately
approved for sharing.

### What remains for formal Phase 1 closure

Even full success here cannot prove uninterrupted true exchange data after
reconnect, production-grade authenticated cross-process RPC, or actual
verified-only default EventBus cutover. Those still require explicit
integration acceptance and code review. No merge, production deployment
or REALITY trading was authorized by the research capture.
