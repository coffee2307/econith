# Phase 1 — verified-only historical research runtime: scope, safety and acceptance

**Review branch:** Draft PR #28. This is **a bounded, read-only historical
research runtime**, NOT a continuously running verified Binance feed, not an
execution engine and not a replacement for any live market-order capability.

## The main entrypoint now has two explicitly disjoint paths

1. **Legacy (default):** `ECONITH_SPOT_INGRESS_POLICY=legacy`.
   Existing FastAPI/TimeEngine/CCXT/world/demo/streamer behavior is unchanged
   and **not** represented as verified.
2. **Verified-only historical research:**
   `ECONITH_SPOT_INGRESS_POLICY=verified_only` with all THREE explicitly
   configured variables:
   - `ECONITH_SPOT_VERIFIED_REPLAY_DB`: absolute path to retained,
     closed, original Binance Spot capture SQLite evidence
   - `ECONITH_SPOT_VERIFIED_REPLAY_SESSION`: original `rec_...` session
   - `ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR`: a new, empty absolute
     directory for *separate* backup/market/receiver DBs

   The preflight refuses missing, non-absolute, nonexistent or nonempty
   evidence paths **before** database/engine/streamer/network startup.
   The original SQLite is opened only as `mode=ro` and copied using a
   SQLite backup. The raw session must be closed, have a valid REST snapshot
   and replay to a synchronized diff-depth book.

   The runtime then checks source→market immutable provenance, projects
   source trades into the dedicated market SQLite DB and insists on a
   separate OS receiver process acknowledging **each** trade through the
   HMAC-authenticated pipe and its independently persisted SQLite receipts.
   No event is published before all original records, first-receipt
   timestamps, lineage mappings and ACK counts are verified.

   Finally it creates a **new** in-memory SIMULATION EventBus and installs
   only the market-data processing subscribers. The verified-only
   publisher capability is issued before EventBus startup. It replays the
   historical `aggTrade` frames with their true original local
   `available_at_ms` metadata and emits **one** final synchronized,
   book-derived top-20 consumer view from the actual Spot REST+diff
   reconstruction, never a raw `depthUpdate` mislabeled as `depth20`.
   Pipeline-derived ticker, volume delta and OBI counts must exactly match
   the verified inputs. The result is a **historical observation**, not
   evidence that the final book was available earlier than its timestamp.

**Deliberate feature restriction:** Unlike legacy mode, this verified-only
branch does NOT call `init_database`, `get_engine`, `engine.startup`,
`CCXTBinanceBridge.connect`, `QuantExecutionBridge`,
`BinanceWebSocketStreamer.start`, macro ingestion, world agents or
trading/simulation bridge startup. The full legacy router objects may
exist at module import time, but a dedicated HTTP middleware **returns
404 on every legacy route** and only permits the read-only
`GET /api/v1/market/verified/status` research observation status. The
legacy metrics WebSocket is explicitly refused (code 1008). Both the
mode-sensitive and source admission gates remain fail-closed.

The verified-only research status declares:
- `mode=SIMULATION`, `historical_replay_only=true`,
  `continuous_live_market=false`
- `original_bytes_read_only=true`,
  `local_receiver_authenticated=true`
- Original count, accepted trades, final synced depth ID, producer/receiver
  ACK counts, emitted original research frames and derived consumer counts
- `history_continuity_verified=false`, `execution_started=false`,
  `orders_submitted=0`, `legacy_streamer_started=false`

### Manual local review without opening a new Binance network connection

After receiving approved Draft PR head in a detached Python 3.12 worktree,
run `pytest tests/test_spot_verified_research_runtime.py -q`. If green,
you can run the opt-in, historical verified-only ASGI app with its **saved**
public source file; do not use the main production environment.

```powershell
$work = Join-Path $env:TEMP "econith-p1-20261017-225926"
$py = Join-Path $work ".venv\Scripts\python.exe"
$env:ECONITH_SPOT_INGRESS_POLICY = "verified_only"
$env:ECONITH_SPOT_VERIFIED_REPLAY_DB = Join-Path $env:TEMP "econith-p1-evidence-v2-20261017-232246\original_binance_spot_public.sqlite"
$env:ECONITH_SPOT_VERIFIED_REPLAY_SESSION = "rec_7544b3d0c2dbad0e44e86dcf"
$env:ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR = Join-Path $env:TEMP ("econith-p1-verified-app-" + (Get-Date -Format "yyyyMMdd-HHmmss"))

# Run locally only, no public interface, no reload or background schedule.
Set-Location $work
& $py -m uvicorn main:app --host 127.0.0.1 --port 8765
```

A separate terminal may inspect
`GET http://127.0.0.1:8765/api/v1/market/verified/status`; all
legacy endpoints including mode switching, metrics, orders, docs and default
HTTP routes must be blocked. Stop with Ctrl+C. The initial verification
starts one independent Python subprocess per source trade, so this is
not intended for online low-latency trading and startup may involve
bounded local SQLite/IPC work.

## Important residual risk — do not claim live production cutover

- This is **offline-replayed** prior captured data, not an uninterrupted live
  authenticated WebSocket feed. No raw Binance message is cryptographically
  signed; TLS is checked in the separately opted-in capture tool.
- The SQLite outbox/receiver model proves durable local acknowledgements,
  not globally atomic distributed exactly-once outcomes or live market order
  behavior. EventBus fanout itself is not a durable per-handler ACK; subscribers
  must not use this in-process stream as authorization to trade.
- Historical data cannot be passed to a live strategy under the pretext that
  all books/trades were available at every earlier timestamp. No training
  split, backtest or PnL claim is implied.
- Production-grade continuous verified-only ingestion, nonblocking relay,
  subscriber-owned exactly-once processing and the full running legacy
  execution graph remain separate engineering and approval gates. The
  default LEGACY mode is preserved intentionally.

Use only an independently approved **research/offline Phase 1 completion**
definition to close the milestone. Do not implicitly redefine a requirement
for continuous live source replacement as complete just because the
historical verified-only HTTP research branch exists.
