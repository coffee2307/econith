# Phase 1 — Original Binance Spot WS/REST capture and as-of replay

**Status:** opt-in and **SIMULATION-only**. No WebSocket or REST connection is
opened by this module; no exchange credentials, network actions, EventBus
publishes, backend main wiring, orders, or capital permission. All endpoint,
session, symbol and source labels are **caller assertions**, not independent
TLS attestation or cryptographic exchange signatures.

## Motivation: existing default streamer is not a verified ingress

`infrastructure/websocket/streamer.py` currently subscribes to the public
`<symbol>@aggTrade` and `<symbol>@depth20@100ms` **partial depth snapshots**,
not Spot `<symbol>@depth@100ms` diff updates. In SIMULATION, that streamer
can modify trade prices and book levels before publishing them to EventBus.
Its bounded transient queue may also discard old frames under backpressure.
Do **not** treat this path as a verified raw capture or pass partial snapshots
to `OfflineSpotDepthSynchronizer`. No code here changes the default path.

## New opt-in evidence path

- `infrastructure/spot_ingress_capture.py`:
  `OfflineSpotCaptureJournal(path,mode_provider)`.
  Start a distinct caller-asserted session with `start_session` and canonical
  symbol, source, Spot combined WS endpoint, and local open time.
- Feed each **original, unmodified** WebSocket combined-stream message to
  `record_ws(session_id, raw_bytes, stream_kind=...)` **before simulation
  overlays or in-memory publication**. When not supplied, receipt time is
  stamped at the call with local `time.time_ns()`; explicit timestamp
  overrides are intended only for deterministic offline tests. Only
  `aggTrade` and Spot `depthUpdate` at `@depth`/`@depth@100ms` are accepted.
  Partial `depth20` snapshots and USD-M Futures `pu` are refused.
- Capture the separately obtained read-only REST
  `GET https://api.binance.com/api/v3/depth?symbol=...` response with
  `record_snapshot(..., rest_endpoint=..., request_symbol=...,
  request_started_at_ms=..., received_at_ms=...)`. The journal binds the
  request symbol, endpoint, session, started time and response receipt.
  REST responses **do not contain** an independently attested exchange symbol;
  request provenance must therefore come from the transport capture layer.
- Accepted raw UTF-8 bytes are written as SQLite BLOB (not reserialized) with
  SHA-256, monotonically ordered receipt timestamp, and session identity,
  using SQLite WAL `synchronous=FULL` and an atomic transaction. Strict JSON
  parsing rejects duplicate keys and NaN/Infinity; schema validation rejects
  wrong symbol/stream, malformed depth levels, Futures `pu`, stale and
  future-dated event clocks. Bounds on body size and records/session fail
  closed, not silent drop. Once closed, a session cannot be reused.
- `replay_as_of(session_id, cutoff_ms)` only selects committed records
  whose local first receipt is <= cutoff and rechecks bytes, hash, session,
  venue, REST request symbol, endpoint, event clock and receipt ordering.
  A replay whose requested `limit` would truncate available evidence is
  refused instead of returning an apparently complete prefix.

## Reconstructing the point-in-time research state

`infrastructure/spot_capture_replay.py` provides
`OfflineSpotCaptureReplayer(capture).replay(session_id,as_of_ms=...)`.
It returns accepted original `aggTrade` records with same-session contiguous
IDs **from the first observed baseline**, plus a Spot order book
reconstructed from the persisted diff buffers and REST snapshot.
Duplicate identical trade events are ignored; conflicting duplicate IDs,
gaps, rollback, or lost depth continuity fail closed. An unsynchronized
book is `None`, not an invented book. This is a research observation
reconstruction, not an exchange fill/execution simulator.

### Minimal deterministic offline usage

```python
import json
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer

journal = OfflineSpotCaptureJournal(
    "research/spot-raw-capture.sqlite",
    mode_provider=lambda: QuantMode.SIMULATION,
)
journal.start_session(
    "ws_001", symbol="BTCUSDT", source="binance_public_simulation",
    ws_endpoint="wss://stream.binance.com:9443/stream",
    opened_at_ms=1780000000000,
)
journal.record_ws(
    "ws_001", json.dumps({
        "stream": "btcusdt@aggTrade",
        "data": {
            "e": "aggTrade", "E": 1780000000000, "T": 1780000000000,
            "s": "BTCUSDT", "a": 101, "f": 101, "l": 101,
            "p": "100", "q": "0.02", "m": False,
        },
    }),
    stream_kind="aggTrade",
    received_at_ms=1780000000100,  # offline fixture time only
)
state = OfflineSpotCaptureReplayer(journal).replay(
    "ws_001", as_of_ms=1780000000100,
)
assert state.trades[0].payload["a"] == 101
assert state.book is None  # no synchronized snapshot+diff available
```

Source of protocol requirements: [Binance Spot WebSocket Streams / local
order book](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md#how-to-manage-a-local-order-book-correctly).
Read-only market data does not require credentials in this offline module.

## Evidence and limitations

The hash detects accidental alteration of a stored raw frame; it is NOT a
signature, protected audit ledger or independently authenticated Binance
source. An attacker with write access to the SQLite DB could replace a raw
body and digest together. Event timestamps/receipt clocks cannot substitute
for measured clock error, real network transport identity, verified REST
request/response, or replay of independently recorded live captures.
Callbacks supplying a backdated explicit receipt time can misstate the
observation date; external capture integration MUST use the immediate local
clock and retain trusted transport/session metadata.

No interaction with the existing market integrity journal is implied by
capturing bytes. This module is not registered by the default streamer; a
future opt-in adapter must capture original frames *before* overlays and
route only validated events to downstream processing. After a websocket
reconnect it must create a new session, capture a fresh REST snapshot,
persist any detected transport outages, and stop on capture/storage failure.

## Tests and Phase 1 exit gates

`tests/test_spot_ingress_capture.py` covers exact-byte persistence, raw
duplicate capture, point-in-time and restart replay, Spot-only envelopes,
REST source/symbol/session binding, tampered frames/digests/metadata,
future/stale clocks, default clock stamping, concurrent append order,
database rollback, session closure and isolation, missing/changed
aggregate IDs, and missing depth intervals. Passing CI on fixtures is not
proof that a genuine Binance WebSocket connection was authenticated.

**Outstanding before Phase 2 sign-off:** verified public TLS transport and
REST read-only capture adapter; durable orchestration/backpressure and
resync policy; authenticated live capture replay under real disconnect,
staleness, clock drift and snapshot latency; independent consumer-owned
cross-process ACK with metrics; explicit cutover that prevents old
`md.depth20` subscribers bypassing validated ingress. No live trading.
