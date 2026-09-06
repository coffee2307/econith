"""ECONITH :: sentinel.circuit_breaker

A classic three-state circuit breaker (master plan, Phase 3, Step 4).

States:
    CLOSED     -- healthy; traffic/orders allowed.
    OPEN       -- tripped; orders rejected (simulated API freeze / reduce-only).
    HALF_OPEN  -- cooldown elapsed; probing whether it is safe to resume.

The breaker trips after ``failure_threshold`` consecutive failures (e.g. the
heartbeat exceeding 300ms three times in a row, or a hard drawdown breach).
After ``reset_timeout_s`` it moves to HALF_OPEN; a success there closes it again.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum

logger = logging.getLogger("econith.sentinel.circuit_breaker")

TransitionHook = Callable[["BreakerState", "BreakerState", str], Awaitable[None]]


class BreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(
        self,
        name: str = "sentinel",
        failure_threshold: int = 3,
        reset_timeout_s: float = 15.0,
        on_transition: TransitionHook | None = None,
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s
        self._on_transition = on_transition
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._last_reason = ""

    # -- introspection --------------------------------------------------------
    @property
    def state(self) -> BreakerState:
        self._maybe_half_open()
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state is BreakerState.OPEN

    @property
    def allows_traffic(self) -> bool:
        """Orders are allowed only when CLOSED or probing in HALF_OPEN."""
        return self.state in (BreakerState.CLOSED, BreakerState.HALF_OPEN)

    @property
    def last_reason(self) -> str:
        return self._last_reason

    # -- state machine --------------------------------------------------------
    def _maybe_half_open(self) -> None:
        if (
            self._state is BreakerState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self._reset_timeout_s
        ):
            self._set(BreakerState.HALF_OPEN, "cooldown elapsed -> probing")

    def _set(self, new: BreakerState, reason: str) -> BreakerState | None:
        if new is self._state:
            return None
        old = self._state
        self._state = new
        self._last_reason = reason
        if new is BreakerState.OPEN:
            self._opened_at = time.monotonic()
        logger.warning("breaker[%s] %s -> %s (%s)", self.name, old, new, reason)
        return old

    async def _emit(self, old: BreakerState | None, reason: str) -> None:
        if old is not None and self._on_transition is not None:
            await self._on_transition(old, self._state, reason)

    async def record_success(self) -> None:
        self._consecutive_failures = 0
        if self._state is BreakerState.HALF_OPEN:
            old = self._set(BreakerState.CLOSED, "probe succeeded -> resumed")
            await self._emit(old, "probe succeeded -> resumed")

    async def trip(self, reason: str) -> None:
        """Force the breaker OPEN immediately (hard fault)."""
        old = self._set(BreakerState.OPEN, reason)
        await self._emit(old, reason)

    async def record_failure(self, reason: str) -> None:
        self._consecutive_failures += 1
        if self._state is BreakerState.HALF_OPEN:
            old = self._set(BreakerState.OPEN, f"probe failed: {reason}")
            await self._emit(old, f"probe failed: {reason}")
            return
        if self._consecutive_failures >= self._failure_threshold:
            old = self._set(
                BreakerState.OPEN,
                f"{self._consecutive_failures}x consecutive: {reason}",
            )
            await self._emit(old, reason)
