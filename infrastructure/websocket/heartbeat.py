"""ECONITH :: infrastructure.websocket.heartbeat

Ping-Pong keepalive for the Binance WebSocket connection. Foundation for the
Sentinel Heartbeat Circuit Breaker (master plan, Phase 3, Step 4).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger("econith.infra.ws.heartbeat")

PingFn = Callable[[], Awaitable[None]]


class Heartbeat:
    """Periodic ping sender with a missed-pong watchdog."""

    def __init__(self, interval_s: float = 180.0, timeout_ms: int = 300) -> None:
        self._interval = interval_s
        self._timeout_ms = timeout_ms
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._missed = 0

    async def start(self, ping: PingFn) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(ping), name="ws-heartbeat")

    async def _loop(self, ping: PingFn) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            try:
                await asyncio.wait_for(ping(), timeout=self._timeout_ms / 1000)
                self._missed = 0
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                self._missed += 1
                logger.warning("missed pong (%d consecutive)", self._missed)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
