"""ECONITH :: core.event_bus

A lightweight asyncio publish/subscribe event bus. It is the backbone the
Core Engine uses to decouple the State Engine, Time Engine and downstream
consumers (Trading, Simulator, Dashboard websockets).

P0 REFACTOR (Mode-Gated Governance Layer)
------------------------------------------
The bus enforces an explicit, strict **institutional isolation** boundary between
the sovereign REALITY data plane and the SIMULATION sandbox. Subscribers may
declare a governance ``domain`` (e.g. :data:`DOMAIN_QUANT` for order-routing /
execution nodes). During dispatch the bus consults the active
:class:`~core.mode.QuantMode`:

  * ``REALITY``    -- any event whose topic matches a governed wildcard prefix
    (e.g. ``world.*``) is *dropped* before it can reach a ``QUANT`` domain
    handler. Simulated world state can never bias a live execution node.
  * ``SIMULATION`` -- world coupling is permitted; the air-gapping of live
    network/order-routing is enforced downstream by the CCXT bridge, which
    refuses to bind live sockets outside REALITY.

The gate is defence-in-depth: it holds even if a future execution node
accidentally subscribes to a simulated topic.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from core.mode import QuantMode, current_mode

logger = logging.getLogger("econith.core.event_bus")

Handler = Callable[["Event"], Awaitable[None]]
ModeProvider = Callable[[], QuantMode]

# --- governance domains ------------------------------------------------------
# Handlers with no domain are ungoverned (default). Execution / order-routing
# nodes should subscribe with ``domain=DOMAIN_QUANT``.
DOMAIN_QUANT: str = "QUANT"

# In REALITY, handlers of a given domain are hard-blocked from receiving any
# event whose topic starts with one of these prefixes.
_REALITY_BLOCKED_PREFIXES: dict[str, tuple[str, ...]] = {
    DOMAIN_QUANT: ("world.",),
}

# Throttle interval (seconds) for the "event dropped by gate" diagnostic log so
# a busy simulated world cannot flood the log during REALITY operation.
_GATE_LOG_THROTTLE_S: float = 5.0


@dataclass(slots=True)
class Event:
    """An immutable message flowing through the bus."""

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class _Subscription:
    """A registered handler plus its governance domain."""

    handler: Handler
    domain: Optional[str] = None


class EventBus:
    """In-process async event bus with topic-based fan-out + mode governance."""

    def __init__(self, mode_provider: Optional[ModeProvider] = None) -> None:
        self._subscribers: dict[str, list[_Subscription]] = defaultdict(list)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._task: Optional[asyncio.Task[None]] = None
        self._running = False
        # Injectable for testing; defaults to the process-global QuantMode.
        self._mode_provider: ModeProvider = mode_provider or current_mode
        self._gate_log_at: dict[str, float] = {}

    def subscribe(
        self, topic: str, handler: Handler, *, domain: Optional[str] = None
    ) -> None:
        """Register ``handler`` for ``topic``.

        Pass ``domain=DOMAIN_QUANT`` for execution / order-routing nodes so the
        mode-gated governance layer can isolate them from simulated topics while
        running in REALITY.
        """
        self._subscribers[topic].append(_Subscription(handler=handler, domain=domain))
        logger.debug("subscribed handler to topic '%s' (domain=%s)", topic, domain)

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

    # -- governance -----------------------------------------------------------
    def _is_gated(self, topic: str, domain: Optional[str]) -> bool:
        """True if a ``domain`` handler must NOT receive ``topic`` under the mode."""
        if not domain:
            return False
        if self._mode_provider() is not QuantMode.REALITY:
            return False
        prefixes = _REALITY_BLOCKED_PREFIXES.get(domain, ())
        return any(topic.startswith(p) for p in prefixes)

    def _note_gate_drop(self, topic: str, domain: str) -> None:
        now = time.monotonic()
        key = f"{domain}:{topic}"
        last = self._gate_log_at.get(key, 0.0)
        if now - last >= _GATE_LOG_THROTTLE_S:
            self._gate_log_at[key] = now
            logger.debug(
                "[MODE GATE] dropped '%s' -> %s handler (REALITY isolation)",
                topic, domain,
            )

    # -- dispatch -------------------------------------------------------------
    async def _dispatch_loop(self) -> None:
        while self._running:
            event = await self._queue.get()
            subs = self._subscribers.get(event.topic, [])
            if not subs:
                continue

            delivered: list[Handler] = []
            for sub in subs:
                if self._is_gated(event.topic, sub.domain):
                    self._note_gate_drop(event.topic, sub.domain or "")
                    continue
                delivered.append(sub.handler)

            if not delivered:
                continue

            results = await asyncio.gather(
                *(h(event) for h in delivered), return_exceptions=True
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.exception(
                        "handler error on '%s': %s", event.topic, result
                    )
