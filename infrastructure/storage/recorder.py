"""ECONITH :: infrastructure.storage.recorder

Crash-safe state recorder (master plan, Phase 1 + Phase 4, Step 3).

Subscribes to the EventBus and persists a throttled stream of market ticks and
the latest Sentinel governance status into local SQLite. On restart the engine
can call ``recover_latest()`` to re-establish the last known state instead of
starting cold.

SQLite writes are synchronous but fast; we throttle market persistence to one
row every ``tick_every`` ticks so the event loop is never disk-bound.
"""
from __future__ import annotations

import json
import logging

from core.event_bus import Event, EventBus
from infrastructure.storage.sqlite import SQLiteStore

logger = logging.getLogger("econith.infra.storage.recorder")


class StateRecorder:
    """Persists market ticks + Sentinel status to SQLite for recovery."""

    def __init__(self, bus: EventBus, store: SQLiteStore | None = None, tick_every: int = 25) -> None:
        self._bus = bus
        self._store = store or SQLiteStore()
        self._tick_every = max(1, tick_every)
        self._tick_count = 0
        self._init_schema()

    def _init_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS market_ticks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                symbol TEXT,
                price REAL
            )
            """
        )
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS sentinel_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                ts TEXT,
                payload TEXT
            )
            """
        )

    def register(self) -> None:
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("sentinel.status", self._on_sentinel_status)
        logger.info("state recorder registered (sqlite)")

    async def _on_ticker(self, event: Event) -> None:
        self._tick_count += 1
        if self._tick_count % self._tick_every != 0:
            return
        p = event.payload
        try:
            self._store.execute(
                "INSERT INTO market_ticks (ts_ms, symbol, price) VALUES (?, ?, ?)",
                (int(p.get("event_ms", 0)), p.get("symbol"), float(p.get("price", 0.0))),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("tick persist skipped: %s", exc)

    async def _on_sentinel_status(self, event: Event) -> None:
        try:
            self._store.execute(
                """
                INSERT INTO sentinel_state (id, ts, payload) VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET ts = excluded.ts, payload = excluded.payload
                """,
                (event.ts.isoformat(), json.dumps(event.payload)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("sentinel persist skipped: %s", exc)

    def recover_latest(self) -> dict | None:
        """Read back the last persisted Sentinel state (for warm restart)."""
        rows = self._store.query("SELECT ts, payload FROM sentinel_state WHERE id = 1")
        if not rows:
            return None
        ts, payload = rows[0]
        try:
            return {"ts": ts, "sentinel": json.loads(payload)}
        except (TypeError, ValueError):
            return None

    def close(self) -> None:
        self._store.close()
