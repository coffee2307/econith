"""Explicit opt-in, research-only public Binance Spot capture transport.

No default pipeline subscriptions and no exchange order functionality. Open a
read-only TLS socket only when capture_public_once(allow_network=True) is
called. Offline capture_open_stream accepts injected transports for repeatable
fault testing. No frame is published before a durable raw SQLite capture.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, AsyncIterable, Awaitable, Callable

from core.mode import QuantMode
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_ingress_capture import (
    CapturedSpotRecord, OfflineSpotCaptureJournal, SpotCaptureError,
)

_PUBLIC_WS = "wss://data-stream.binance.vision/stream"
_PUBLIC_REST = "https://data-api.binance.vision/api/v3/depth"


class SpotTransportError(SpotCaptureError):
    """Fail-closed capture error carrying a non-sensitive diagnostic code."""

    def __init__(self, message: str, *, reason: str = "TRANSPORT_ERROR") -> None:
        if not reason.isascii() or not reason.replace("_", "").isalnum() or len(reason) > 80:
            raise ValueError("invalid static capture reason")
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class SpotTransportResult:
    session_id: str
    symbol: str
    websocket_frames: int
    snapshot_captures: int
    last_received_at_ms: int
    queue_high_watermark: int
    depth_status: str
    last_update_id: int


class OptInSpotIngressAdapter:
    """Capture a bounded public read-only Spot stream and REST snapshot.

    WebSocket messages and snapshot responses are independently stamped
    immediately when their awaitable returns, then fed into a bounded FIFO
    writer. Queue overflow is terminal, not a lossy optimization. A single
    writer commits all captures in received order, and replay must reconstruct
    a synchronized book before this operation can report success.

    Injected transports do not prove TLS peer identity. Use
    capture_public_once() for a standard hostname-verified TLS connection;
    even that is transport-level peer validation, not a signed exchange
    event or verified local wall-clock.
    """

    def __init__(
        self, journal: OfflineSpotCaptureJournal, *, symbol: str,
        clock_ms: Callable[[], int] | None = None,
        queue_capacity: int = 128,
        snapshot_limit: int = 1000,
    ) -> None:
        if not isinstance(journal, OfflineSpotCaptureJournal):
            raise ValueError("an immutable offline Spot capture journal is required")
        journal._simulation_only()
        journal._symbol(symbol)
        if type(queue_capacity) is not int or not 2 <= queue_capacity <= 8192:
            raise ValueError("queue_capacity must be between 2 and 8192")
        if snapshot_limit not in (100, 500, 1000, 5000):
            raise ValueError("invalid explicit read-only depth snapshot limit")
        self.journal = journal
        self.symbol = symbol
        self.clock = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.queue_capacity = queue_capacity
        self.snapshot_limit = snapshot_limit

    def _clock(self) -> int:
        value = self.clock()
        return self.journal._positive(value, "transport local receipt clock")

    def _kind(self, body: str | bytes) -> str:
        _, parsed = self.journal._parse(body)
        name = parsed.get("stream")
        sym = self.symbol.lower()
        if name == f"{sym}@aggTrade":
            return "aggTrade"
        if name in (f"{sym}@depth", f"{sym}@depth@100ms"):
            return "depthUpdate"
        raise SpotTransportError(
            "unexpected combined market stream; no silent discard",
            reason="UNEXPECTED_STREAM",
        )

    async def capture_open_stream(
        self, stream: AsyncIterable[str | bytes],
        snapshot_fetch: Callable[[str, int], Awaitable[str | bytes]],
        *, session_id: str, source: str = "offline_fixture",
        ws_endpoint: str = _PUBLIC_WS,
        rest_endpoint: str = _PUBLIC_REST,
        max_frames: int = 300,
        timeout_s: float = 30.0,
        on_persisted: Callable[[CapturedSpotRecord], Awaitable[None]] | None = None,
        idle_timeout_s: float | None = None,
        require_frame_limit: bool = False,
    ) -> SpotTransportResult:
        """No network is opened here; caller supplies an already-opened stream.

        The first recorded Spot diff triggers a concurrent read-only REST
        snapshot request. Independent readers enqueue in first-receipt order.
        Both stop on malformed evidence, resource overflow or transport loss.
        A healthy session requires an actually bridged book, not only TCP EOF.
        """
        self.journal._simulation_only()
        if type(max_frames) is not int or not 1 <= max_frames <= 4998:
            raise ValueError("max_frames must fit bounded full-session replay")
        if (
            type(timeout_s) not in (int, float)
            or not 0 < timeout_s <= 600
            or timeout_s != timeout_s
        ):
            raise ValueError("invalid capture timeout")
        if (idle_timeout_s is not None and (
            type(idle_timeout_s) not in (int,float)
            or not 0.1 <= idle_timeout_s <= 120
            or idle_timeout_s != idle_timeout_s)):
            raise ValueError("invalid bounded websocket read idle timeout")
        if type(require_frame_limit) is not bool:
            raise ValueError("invalid source completeness policy")
        if not callable(snapshot_fetch):
            raise TypeError("a read-only REST snapshot fetcher is required")
        if on_persisted is not None and not callable(on_persisted):
            raise TypeError("incremental committed-record verifier must be callable")
        opened = self._clock()
        self.journal.start_session(
            session_id, symbol=self.symbol, source=source,
            ws_endpoint=ws_endpoint, opened_at_ms=opened,
        )
        fifo: asyncio.Queue[tuple[str, Any, int]] = asyncio.Queue(
            maxsize=self.queue_capacity
        )
        captured_ws = 0
        captured_snapshots = 0
        high_watermark = 0
        last_ms = opened
        reader_done = False
        frame_limit_reached = False
        snapshot_started = False
        snapshot_done = False
        reader: asyncio.Task[None] | None = None
        snapshot_task: asyncio.Task[None] | None = None

        async def _queue(kind: str, data: Any, ts: int) -> None:
            nonlocal high_watermark
            # Observe depth on enqueue, not just dequeue; a fully saturated
            # reader could fail before the writer ever sees its true peak.
            try:
                fifo.put_nowait((kind, data, ts))
                high_watermark = max(high_watermark, fifo.qsize())
            except asyncio.QueueFull as exc:
                high_watermark = max(high_watermark, self.queue_capacity)
                raise SpotTransportError(
                    "ingress backpressure overflow: abort session",
                    reason="QUEUE_BACKPRESSURE",
                ) from exc

        async def _read() -> None:
            count = 0
            try:
                iterator = aiter(stream)
                while True:
                    try:
                        if idle_timeout_s is None:
                            raw = await anext(iterator)
                        else:
                            raw = await asyncio.wait_for(
                                anext(iterator), timeout=idle_timeout_s,
                            )
                    except StopAsyncIteration:
                        if require_frame_limit:
                            raise SpotTransportError(
                                "public websocket ended before full bounded observation",
                                reason="WS_EARLY_EOF",
                            )
                        break
                    except asyncio.TimeoutError as exc:
                        raise SpotTransportError(
                            "websocket stopped delivering market evidence",
                            reason="WS_IDLE_TIMEOUT",
                        ) from exc
                    received = self._clock()
                    count += 1
                    await _queue("ws", raw, received)
                    if count >= max_frames:
                        # Bounded *successful* research capture: deliberately
                        # stop reading after max_frames; never infer continuity
                        # beyond the bounded observation interval. The writer
                        # still must persist all queued originals and prove
                        # a synchronized snapshot before returning success.
                        await fifo.put(("frame_limit", None, self._clock()))
                        return
            except Exception as exc:
                await fifo.put(("ws_error", exc, self._clock()))
            else:
                await fifo.put(("eof", None, self._clock()))

        async def _snapshot() -> None:
            try:
                request_started_at = self._clock()
                raw = await snapshot_fetch(self.symbol, self.snapshot_limit)
                received = self._clock()
                await _queue("snapshot", (raw, request_started_at), received)
            except Exception as exc:
                await fifo.put(("snapshot_error", exc, self._clock()))

        try:
            reader = asyncio.create_task(_read(), name="spot-capture-ws-read")
            async with asyncio.timeout(timeout_s):
                while not (reader_done and (not snapshot_started or snapshot_done)):
                    kind, value, received = await fifo.get()
                    high_watermark = max(high_watermark, fifo.qsize() + 1)
                    if kind in ("ws_error", "snapshot_error"):
                        reason = (
                            "WS_READ_FAILED" if kind == "ws_error"
                            else "REST_SNAPSHOT_FAILED"
                        )
                        if isinstance(value, SpotTransportError):
                            reason = value.reason
                        raise SpotTransportError(
                            f"capture reader failed: {type(value).__name__}: {value}",
                            reason=reason,
                        ) from value
                    if kind in ("eof", "frame_limit"):
                        reader_done = True
                        frame_limit_reached = kind == "frame_limit"
                        continue
                    if kind == "ws":
                        stream_kind = self._kind(value)
                        committed_id = self.journal.record_ws(
                            session_id, value, stream_kind=stream_kind,
                            received_at_ms=received,
                        )
                        # Callback is invoked only AFTER SQLite COMMIT and a
                        # fresh strict row/fingerprint/source verification.
                        # A slow verifier stalls the single writer; the
                        # bounded incoming FIFO aborts rather than dropping.
                        if on_persisted is not None:
                            await on_persisted(
                                self.journal.verify_captured_record(session_id, committed_id)
                            )
                        captured_ws += 1
                        last_ms = received
                        if stream_kind == "depthUpdate" and not snapshot_started:
                            snapshot_started = True
                            snapshot_task = asyncio.create_task(
                                _snapshot(), name="spot-capture-rest-snapshot"
                            )
                    elif kind == "snapshot":
                        raw, requested = value
                        committed_id = self.journal.record_snapshot(
                            session_id, raw, rest_endpoint=rest_endpoint,
                            request_symbol=self.symbol,
                            request_started_at_ms=requested,
                            received_at_ms=received,
                        )
                        if on_persisted is not None:
                            await on_persisted(
                                self.journal.verify_captured_record(session_id, committed_id)
                            )
                        captured_snapshots += 1
                        last_ms = received
                        snapshot_done = True
                    else:
                        raise SpotTransportError("unrecognized ingest capture event")
            if captured_snapshots != 1:
                raise SpotTransportError(
                    "frame bound reached before synchronized snapshot"
                    if frame_limit_reached else
                    "no captured REST depth snapshot in session",
                    reason=("FRAME_LIMIT_UNSYNCED" if frame_limit_reached
                            else "NO_REST_SNAPSHOT"),
                )
            state = OfflineSpotCaptureReplayer(self.journal).replay(
                session_id, as_of_ms=last_ms, limit=5000,
            )
            if state.book is None:
                raise SpotTransportError(
                    "captured Spot depth did not establish a usable synchronized book",
                    reason=("FRAME_LIMIT_UNSYNCED" if frame_limit_reached
                            else "BOOK_NOT_SYNCED"),
                )
            return SpotTransportResult(
                session_id, self.symbol, captured_ws,
                captured_snapshots, last_ms, high_watermark,
                state.depth_status, state.book.last_update_id,
            )
        except TimeoutError as exc:
            timeout_error = SpotTransportError(
                "capture timed out, session invalid",
                reason="CAPTURE_TIMEOUT",
            )
            timeout_error.ingress_queue_high_watermark = high_watermark
            raise timeout_error from exc
        except SpotCaptureError as exc:
            exc.ingress_queue_high_watermark = high_watermark
            raise
        finally:
            for task in (reader, snapshot_task):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (reader, snapshot_task) if task is not None),
                return_exceptions=True,
            )
            # Close the session on *both* success and failure; never reuse a
            # suspect capture session after EOF, writer error or cancellation.
            # If the local clock regressed, closing fails closed as well.
            self.journal.close_session(session_id, closed_at_ms=self._clock())

    async def capture_public_once(
        self, *, allow_network: bool = False,
        session_id: str | None = None,
        max_frames: int = 300,
        timeout_s: float = 30.0,
        inject_disconnect_after_frames: int | None = None,
        on_persisted: Callable[[CapturedSpotRecord], Awaitable[None]] | None = None,
        idle_timeout_s: float = 30.0,
    ) -> SpotTransportResult:
        """Explicit public TLS market-data-only capture; no API keys or orders.

        Uses pinned public WS/REST hosts, certificate validation, no redirects,
        no environment proxies, and a single GET /api/v3/depth.
        Never invoked by default startup or the existing EventBus.
        """
        self.journal._simulation_only()
        if allow_network is not True:
            raise PermissionError("public network capture requires explicit opt-in")
        if inject_disconnect_after_frames is not None and (
            type(inject_disconnect_after_frames) is not int
            or not 1 <= inject_disconnect_after_frames < max_frames
        ):
            raise ValueError("injected disconnect must precede bounded frame limit")
        import httpx
        import websockets

        sym = self.symbol.lower()
        url = (
            f"{_PUBLIC_WS}?streams={sym}@aggTrade/"
            f"{sym}@depth@100ms"
        )

        async def get_snapshot(symbol: str, limit: int) -> bytes:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                follow_redirects=False,
                trust_env=False,
                verify=True,
                headers={"Accept-Encoding": "identity"},
            ) as client:
                response = await client.get(
                    _PUBLIC_REST, params={"symbol": symbol, "limit": limit},
                )
                if response.status_code != 200:
                    raise SpotTransportError(
                        f"REST depth refused HTTP {response.status_code}",
                        reason="REST_HTTP_FAILED",
                    )
                if len(response.content) > self.journal.max_body_bytes:
                    raise SpotTransportError(
                        "REST snapshot exceeded raw capture size limit",
                        reason="REST_BODY_OVERSIZE",
                    )
                return response.content

        async with websockets.connect(
            url, open_timeout=10, close_timeout=5,
            max_size=self.journal.max_body_bytes, max_queue=16,
            ping_interval=20, ping_timeout=20,
        ) as connection:
            stream = connection
            if inject_disconnect_after_frames is not None:
                async def _deliberately_interrupt():
                    count = 0
                    async for frame in connection:
                        yield frame
                        count += 1
                        if count == inject_disconnect_after_frames:
                            # Deliberate read-only *local* WS interruption:
                            # the adapter must mark the old source session
                            # failed and never imply recovered historical data.
                            raise SpotTransportError(
                                "controlled WebSocket disconnect injected",
                                reason="INJECTED_WS_DISCONNECT",
                            )
                    # Early remote EOF must not quietly appear as a
                    # successfully injected disconnect.
                    raise SpotTransportError(
                        "public WebSocket closed before disconnect injection",
                        reason="WS_EARLY_EOF",
                    )
                stream = _deliberately_interrupt()
            return await self.capture_open_stream(
                stream, get_snapshot,
                session_id=session_id or f"ws_{secrets.token_hex(12)}",
                source="binance_public_simulation",
                ws_endpoint=_PUBLIC_WS, rest_endpoint=_PUBLIC_REST,
                max_frames=max_frames, timeout_s=timeout_s,
                on_persisted=on_persisted,
                idle_timeout_s=idle_timeout_s,
                require_frame_limit=True,
            )
