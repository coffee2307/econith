"""ECONITH :: infrastructure.storage.sqlite

Local SQLite helper used for trade-lifecycle logging and crash-safe State
Recovery (master plan, Phase 4, Step 3). Optimised for fast overwrite on the
trading VPS.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("econith.infra.storage.sqlite")

DEFAULT_PATH = Path("datasets/econith.sqlite")


class SQLiteStore:
    """Tiny synchronous SQLite wrapper for local state durability."""

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        cur = self._conn.execute(sql, params)
        self._conn.commit()
        return cur

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()
