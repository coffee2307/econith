# Phase 1 research-only Binance Spot order-book sync

**Status:** isolated offline implementation. No default `main.py`/EventBus
registration, no network client, no external source authentication, no capital
authorization. Applies only to Spot `<symbol>@depth` **diff updates** and a
separately obtained REST `/api/v3/depth` snapshot. It deliberately does **not**
reuse `aggTrade` +1 IDs or interpret a partial top-N depth snapshot as a diff.

Official protocol: [Binance Spot WebSocket Streams — local order book](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#how-to-manage-a-local-order-book-correctly).
Binance specifies buffer-then-snapshot, update ID interval `U` through `u`,
discard `u <= lastUpdateId`, and resnapshot if `U > currentUpdateId+1`.
Snapshot depth has a 5000-level-per-side limit; this is not proof of complete
order book coverage at deeper levels.

## Research component

`infrastructure/spot_depth_sync.py` defines
`OfflineSpotDepthSynchronizer(symbol, source, session_id, mode_provider)`.
Each instance is bound to exactly one caller-asserted Spot source, symbol, and
socket session. It never fetches a snapshot, opens WebSocket connections,
publishes to EventBus or places orders.

State machine:

- `BUFFERING`: collect original diff frames, reject wrong symbols, Futures
  `pu` envelopes, malformed/nonfinite prices, impossible `U/u` ranges,
  future/stale source timestamps, order inversions and excessive buffering.
- `AWAITING_NEW_SNAPSHOT` response: a candidate REST snapshot has
  `lastUpdateId < first buffered U`. Retain the buffer and require a newer
  snapshot; never fabricate missing updates.
- `WAITING_BRIDGE`: snapshot accepted but buffered messages did not yet
  bridge the current update ID. **Do not expose** the book in this state.
- `SYNCED`: only after diff `U <= lastUpdateId+1 <= u` applies. Older
  intervals are ignored; absolute quantity replaces prior quantity, zero
  deletes a price level. Best bid must remain strictly below best ask.
- `RESET_REQUIRED`: detected update gap, crossed/empty book, invalid
  diff, regressed receipt time, or memory limit. Clear book and stop.
  Explicit `reconnect(new_session_id)` is required, followed by a new
  buffer and new REST snapshot; old book levels must not be published again.

The `view(as_of_ms=...)` API includes last update ID, best-to-worst bids,
asks, session/source labels, local first snapshot receipt and latest
input-availability timestamp. A view is refused when the book is not fully
synchronized or the requested as-of precedes the latest observed input.
There is **no point-in-time historical book storage**: `view` is only the
current eligible materialized book and cannot reconstruct prior states.

## Deterministic offline usage

```python
from core.mode import QuantMode
from infrastructure.spot_depth_sync import OfflineSpotDepthSynchronizer

sync = OfflineSpotDepthSynchronizer(
    symbol="BTCUSDT", market="binance_spot",
    source="binance_public_simulation", session_id="ws_session_1",
    mode_provider=lambda: QuantMode.SIMULATION,
)
sync.ingest_diff(
    {"e": "depthUpdate", "E": 1780000000000, "s": "BTCUSDT",
     "U": 100, "u": 102, "b": [["100", "3"]], "a": []},
    received_at_ms=1780000000100,
)
result = sync.load_snapshot(
    {"lastUpdateId": 100, "bids": [["100", "2"]],
     "asks": [["101", "4"]]},
    received_at_ms=1780000000200, session_id="ws_session_1",
)
assert result.status == "SYNCED"
assert sync.view().last_update_id == 102
```

**Do not run the example as evidence of exchange source authentication**:
timestamps, payloads, transport scope and snapshot identity here are offline
fixtures. Real capture must stamp receipt before any simulation overlays, bind
the verified TLS/websocket session and read-only REST request/response to the
same instrument, measure clock uncertainty, and persist immutable raw frames
with bounded backpressure.

## Regression contract

`tests/test_spot_depth_sync.py` asserts snapshot too old, first-event
bridging, overlap, late/duplicate updates, gap reset, reconnect isolation,
buffer overflow, crossed book rejection, invalid levels/market/symbol,
future/stale event clock, monotonic local receipt, as-of checks, memory
bounds, no usable partial state, and hard refusal in REALITY mode.

**Remaining for Phase 1 sign-off:** durable capture of both REST snapshots and
original websocket frames; session-scoped provenance; response latency and
retry budgets; reconnect/recovery simulation using recorded authentic
captures; cross-process delivery with durable idempotent receiver-owned ACK;
metrics and backpressure/dead-letter; validation that default pipeline
subscribers cannot bypass verified admission. Local CI pass alone does not
certify completeness, strategy alpha, or execution safety.
