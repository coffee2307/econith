"""ECONITH :: core.event_bus

A lightweight asyncio publish/subscribe event bus. It is the backbone the
Core Engine uses to decouple the State Engine, Time Engine and downstream
consumers (Trading, Simulator, Dashboard websockets).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("econith.core.event_bus")

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(slots=True)
class Event:
    """An immutable message flowing through the bus."""

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    """In-process async event bus with topic-based fan-out."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._running = False

    def subscribe(self, topic: str, handler: Handler) -> None:
        self._subscribers[topic].append(handler)
        logger.debug("subscribed handler to topic '%s'", topic)

    async def publish(self, topic: str, **payload: Any) -> None:
        await self._queue.put(Event(topic=topic, payload=payload))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="event-bus")
        logger.info("event bus started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("event bus stopped")

    async def _dispatch_loop(self) -> None:
        while self._running:
            event = await self._queue.get()
            handlers = self._subscribers.get(event.topic, [])
            if not handlers:
                continue
            results = await asyncio.gather(
                *(h(event) for h in handlers), return_exceptions=True
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.exception("handler error on '%s': %s", event.topic, result)
