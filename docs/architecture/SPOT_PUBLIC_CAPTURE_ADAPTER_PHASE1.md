# Phase 1 — opt-in public Spot WebSocket + REST capture adapter

**Scope:** opt-in, SIMULATION-only market-data research. No default app
registration, EventBus publish, exchange keys, orders, position management,
portfolio effects, production deployment or broker ACK.

Implementation:
- `infrastructure/spot_public_capture_adapter.py`
- `tests/test_spot_public_capture_adapter.py`
- Previously staged immutable source journal:
  `infrastructure/spot_ingress_capture.py`
- Point-in-time verifier/replayer: `infrastructure/spot_capture_replay.py`
- Spot book algorithm: `infrastructure/spot_depth_sync.py`

## Explicit public read-only transport

`OptInSpotIngressAdapter(journal, symbol="BTCUSDT")` exposes:

1. `capture_open_stream(async_stream, snapshot_fetch, session_id=...)`
   for **injected** existing transports and deterministic tests. It performs
   no network IO independently and does NOT attest the peer.
2. `capture_public_once(allow_network=True)` is an explicit, bounded
   public read-only network operation:
   - `wss://data-stream.binance.vision/stream?streams=btcusdt@aggTrade/btcusdt@depth@100ms`
   - `GET https://data-api.binance.vision/api/v3/depth?symbol=BTCUSDT&limit=1000`
   - HTTPS uses httpx's verified certificates, `trust_env=False` (no
     environment proxy), and `follow_redirects=False`. WebSocket uses
     the websockets library's default hostname/certificate validation.
     No credentials, trading routes, or signed endpoints are used.
   - The connector is only imported and invoked by this explicit method.
     `allow_network=False` refuses access before opening a socket.

**Runtime-independent test harnesses MUST NOT be described as authenticated
Binance sessions.** Their endpoint identities are test declarations. In live
capture, host certificate verification authenticates a TLS peer, not an
individual trade, a timestamp, a quote, or the venue's complete delivery.

## Why the adapter uses a single bounded writer

- Raw combined-stream WebSocket bytes are stamped at receipt, before any
  simulation-price overlay. No EventBus publish occurs.
- The first Spot `depthUpdate` triggers **one concurrent** read-only
  snapshot request for the same session/symbol. The request-start and
  response-receipt times are stamped and persisted. The writer's bounded
  queue receives both WS and REST observations, retaining their receipt
  order in one SQLite session ledger.
- A saturated queue, a corrupt/unknown stream, clock regression, malformed
  snapshot, storage failure, network/snapshot failure, replay gap, insufficient
  snapshot bridge or timeout **aborts** the session. The implementation
  neither replaces the oldest item nor marks a partial order book healthy.
- Existing `OfflineSpotCaptureReplayer` checks persisted evidence and
  requires `depth_status=SYNCED` before the adapter returns success.
  The finite session is closed whether the operation succeeds or fails;
  new captures require distinct session IDs and fresh REST snapshots.
- `capture_open_stream` has bounded queue capacity (128 default), finite
  maximum WebSocket frames (300 default, <=4998), complete offline replay
  and session timeout (30 seconds default, <=600).
  This is an intentionally **bounded capture experiment** rather than a
  persistent real-time ingestion service.

## Opt-in example

```python
import asyncio
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter

async def main():
    journal = OfflineSpotCaptureJournal(
        "research/spot-capture.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    adapter = OptInSpotIngressAdapter(journal, symbol="BTCUSDT")
    # Initiates public market-data-only TLS/HTTPS requests.
    # Never runs automatically and never creates any exchange order.
    outcome = await adapter.capture_public_once(
        allow_network=True, max_frames=300, timeout_s=60.0,
    )
    print(outcome)

if __name__ == "__main__":
    asyncio.run(main())
```

The example needs the pinned `websockets` and `httpx` dependencies from
the project's `requirements.txt`. Do NOT place it in `main.py` or a
production deployment workflow. The existing streamer currently uses
`@depth20@100ms` partial snapshots, and can modify simulated frames; it
is **not** a drop-in adapter for `@depth@100ms` diff-depth continuity.

## Test coverage and missing controls

`tests/test_spot_public_capture_adapter.py` injects async transports to
test original-byte retention, bridged depth, TLS/REST host selection with
mocked sockets, false-completeness rejection, partial-depth refusal,
capacity backpressure, network errors, REST errors, timeout, SQLite failure,
mode transition and new-session recovery. No public internet connection
is made in CI.

Remaining pre-Phase-2 acceptance criteria:

1. Controlled real public data capture, peer/response identity evidence and
   independent replay on captured files; measure wall-clock error.
2. Multi-session reconnect controller with bounded retries and durable
   outage/downtime ledger. `capture_public_once` is deliberately single-
   session and does **not** silently reconnect or certify recovery coverage.
3. Durable cross-process consumer-owned inbox ACK; queue/backlog metrics,
   retry/dead-letter policy and documented storage budget.
4. End-to-end fault-injection and verification that no default producer or
   consumer bypasses verified ingress. A single stream from one symbol is
   not the same as comprehensive, multiasset capture.

Sources:
- [Binance Spot WebSocket streams](https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md)
- [Binance market-data-only endpoints](https://github.com/binance/binance-spot-api-docs/blob/master/faqs/market_data_only.md)

No alpha, expected returns, production safety or live-trading permission
is implied by passing these offline engineering checks.
