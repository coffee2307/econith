"""Opt-in pinned public Spot WSS lease reused between bounded research segments.

The lease owns exactly one public TLS WebSocket and one read-only TLS
REST client; it never supplies exchange credentials, sends orders, retries
unverified data, or implies that multiple individually sealed segments
provide gap-free venue history. A broken lease must be discarded before
the next segment. Only this module creates verified-public transport
claims; injected fixture streams remain explicitly offline research.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from infrastructure.spot_public_capture_adapter import (
    _PUBLIC_REST, _PUBLIC_WS, SpotTransportError,
)


class PinnedSpotLeaseError(RuntimeError):
    """Pinned public Spot transport invalid, closed or disallowed."""


_LEASE_TOKEN=object()


class PinnedPublicSpotLease:
    def __init__(self, *, _token: object,
                 socket: object, rest_client: object,
                 max_body_bytes: int):
        if _token is not _LEASE_TOKEN:
            raise PinnedSpotLeaseError("lease cannot be constructed externally")
        self._socket=socket
        self._rest=rest_client
        self._limit=max_body_bytes
        self._closed=False
        self.segments_started=0

    def ensure_open(self) -> None:
        if self._closed:
            raise PinnedSpotLeaseError("public Spot lease no longer valid")

    @property
    def stream(self):
        self.ensure_open()
        return self._socket

    async def snapshot(self, symbol: str, limit: int) -> bytes:
        self.ensure_open()
        if symbol != "BTCUSDT" or type(limit) is not int or not 100 <= limit <= 5000:
            raise PinnedSpotLeaseError("public lease snapshot request out of scope")
        response=await self._rest.get(
            _PUBLIC_REST,params={"symbol":symbol,"limit":limit},
        )
        if response.status_code!=200:
            raise SpotTransportError(
                "pinned Spot REST snapshot refused",
                reason="REST_HTTP_FAILED",
            )
        if len(response.content)>self._limit:
            raise SpotTransportError(
                "pinned Spot REST snapshot over source byte limit",
                reason="REST_BODY_OVERSIZE",
            )
        return response.content

    def invalidate(self) -> None:
        self._closed=True


@asynccontextmanager
async def open_pinned_public_spot_lease(
    *, allow_public_market_data: bool=False,
    max_body_bytes: int=1_000_000,
) -> AsyncIterator[PinnedPublicSpotLease]:
    if allow_public_market_data is not True:
        raise PermissionError("explicit public market data opt-in required")
    if type(max_body_bytes) is not int or not 1024 <= max_body_bytes <= 2_000_000:
        raise PinnedSpotLeaseError("invalid bounded public payload ceiling")
    import httpx
    import websockets
    socket_url=(
        f"{_PUBLIC_WS}?streams=btcusdt@aggTrade/btcusdt@depth@100ms"
    )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),follow_redirects=False,
        trust_env=False,verify=True,headers={"Accept-Encoding":"identity"},
    ) as rest:
        async with websockets.connect(
            socket_url,open_timeout=10,close_timeout=5,
            max_size=max_body_bytes,max_queue=16,
            ping_interval=20,ping_timeout=20,
        ) as socket:
            lease=PinnedPublicSpotLease(
                _token=_LEASE_TOKEN,
                socket=socket,rest_client=rest,
                max_body_bytes=max_body_bytes,
            )
            try:
                yield lease
            finally:
                lease.invalidate()
