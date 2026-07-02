"""ECONITH Quant :: recovery.state

Trade-lifecycle state logging for crash recovery (master plan, Phase 4, Step 3).

Every order intent and child-order lifecycle transition is written to a local
SQLite ledger optimised for fast overwrite. On restart, ``open_orders()`` lets
the bot reconcile in-memory state against the exchange instead of cold-starting.
"""
from __future__ import annotations

import logging
import time

from infrastructure.storage.sqlite import SQLiteStore

logger = logging.getLogger("econith.quant.recovery.state")


class TradeStateStore:
    """SQLite-backed ledger of order intents and lifecycle transitions."""

    def __init__(self, store: SQLiteStore | None = None) -> None:
        self._store = store or SQLiteStore(path="datasets/trades.sqlite")
        self._init_schema()

    def _init_schema(self) -> None:
        self._store.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts_ms INTEGER NOT NULL,
                symbol TEXT,
                side TEXT,
                quantity REAL,
                limit_price REAL,
                algo TEXT,
                status TEXT,
                reason TEXT
            )
            """
        )

    def record(
        self,
        symbol: str,
        side: str,
        quantity: float,
        limit_price: float,
        algo: str,
        status: str,
        reason: str = "",
    ) -> None:
        try:
            self._store.execute(
                """
                INSERT INTO orders
                    (ts_ms, symbol, side, quantity, limit_price, algo, status, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (int(time.time() * 1000), symbol, side, quantity, limit_price, algo, status, reason),
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("order persist skipped: %s", exc)

    def open_orders(self) -> list[dict]:
        """Return orders still considered live (for restart reconciliation)."""
        rows = self._store.query(
            "SELECT symbol, side, quantity, limit_price, algo, status "
            "FROM orders WHERE status IN ('SUBMITTED', 'WORKING') ORDER BY id DESC"
        )
        keys = ("symbol", "side", "quantity", "limit_price", "algo", "status")
        return [dict(zip(keys, row)) for row in rows]

    def close(self) -> None:
        self._store.close()
