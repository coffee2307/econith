"""Durable receipt for the *research indicator observer* only.

This ledger is opt-in and local to the SIMULATION research process. The
MarketDataPipeline's 3 derived topics keep their source coordinates: immutable
session ID, source record ID, ordinal and original local receipt timestamp.

A row's canonical derived JSON and cursor commit in ONE SQLite transaction.
Crashes before COMMIT are replayable; identical replays after COMMIT return
the original receipt, conflicting values and skipped ordinals fail closed.

This is NOT a durable receipt for any other EventBus subscriber, does not
make EventBus transactional, and does not authorize positions/orders.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from core.mode import QuantMode
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor

_TOPICS = frozenset(("md.ticker", "indicator.volume_delta", "indicator.obi"))


class DerivedResearchReceiptError(ValueError):
    """A research derived event cannot acquire a durable consumer receipt."""


@dataclass(frozen=True)
class DerivedResearchReceipt:
    session_id: str
    consumer_topic: str
    ordinal: int
    source_record_id: str
    source_available_at_ms: int
    receipt_id: str
    replayed: bool


class OfflineDerivedResearchReceiptLedger:
    """One independent SQLite per research run, sorted by per-topic ordinal.

    Source scope is bound to the original 'rec_' session. Each derived topic
    independently must consume ordinals 1,2,3,... with no skip. A replay may
    recheck any exact existing ordinal without incrementing the cursor.
    """

    def __init__(
        self, path: str | Path, *,
        mode_provider: Callable[[], QuantMode],
    ) -> None:
        if not callable(mode_provider):
            raise DerivedResearchReceiptError("SIMULATION mode provider required")
        self.path = Path(path)
        self._mode_provider = mode_provider
        self._simulation_only()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_research_derived_receipts (
                    session_id TEXT NOT NULL,
                    consumer_topic TEXT NOT NULL,
                    ordinal INTEGER NOT NULL CHECK (ordinal > 0),
                    source_record_id TEXT NOT NULL,
                    source_available_at_ms INTEGER NOT NULL,
                    projection_json TEXT NOT NULL,
                    projection_digest TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    PRIMARY KEY(session_id, consumer_topic, ordinal),
                    UNIQUE(session_id, consumer_topic, source_record_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_research_derived_cursors (
                    session_id TEXT NOT NULL,
                    consumer_topic TEXT NOT NULL,
                    last_ordinal INTEGER NOT NULL CHECK (last_ordinal > 0),
                    PRIMARY KEY(session_id, consumer_topic)
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), isolation_level=None, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
        finally:
            conn.close()

    def _simulation_only(self) -> None:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("derived research receipts require SIMULATION")

    @staticmethod
    def _validate(
        topic: str, session_id: str, ordinal: int,
        source_id: str, available_at_ms: int,
        projection: Mapping[str, Any],
    ) -> tuple[str, str, str]:
        if topic not in _TOPICS:
            raise DerivedResearchReceiptError("unreviewed research consumer topic")
        if (
            type(session_id) is not str or not session_id.startswith("rec_")
            or len(session_id) > 48
        ):
            raise DerivedResearchReceiptError("original capture session required")
        if type(ordinal) is not int or ordinal <= 0:
            raise DerivedResearchReceiptError("positive source ordinal required")
        if (
            type(source_id) is not str or len(source_id) > 160
            or not source_id.startswith(session_id + ":")
        ):
            raise DerivedResearchReceiptError("source ID not tied to original session")
        if type(available_at_ms) is not int or available_at_ms <= 0:
            raise DerivedResearchReceiptError("invalid original local receipt")
        try:
            body, digest = OfflineMarketProjectionCursor._serialize(projection)
        except (TypeError, ValueError) as exc:
            raise DerivedResearchReceiptError("noncanonical derived consumer result") from exc
        if len(body.encode("utf-8")) > 65536:
            raise DerivedResearchReceiptError("derived result exceeds bounded size")
        identity = json.dumps(
            [session_id, topic, ordinal, source_id, available_at_ms, digest],
            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
        receipt_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        return body, digest, receipt_id

    def accept(
        self, *, topic: str, session_id: str, ordinal: int,
        source_record_id: str, source_available_at_ms: int,
        projection: Mapping[str, Any],
        inject_crash_before_commit: bool = False,
    ) -> DerivedResearchReceipt:
        """Atomically store source-bound observer projection + durable cursor."""
        self._simulation_only()
        serial, digest, receipt_id = self._validate(
            topic, session_id, ordinal, source_record_id,
            source_available_at_ms, projection,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute("""
                    SELECT * FROM offline_research_derived_receipts
                    WHERE session_id=? AND consumer_topic=? AND ordinal=?
                """, (session_id, topic, ordinal)).fetchone()
                if existing is not None:
                    if (
                        existing["source_record_id"] != source_record_id
                        or existing["source_available_at_ms"] != source_available_at_ms
                        or existing["projection_json"] != serial
                        or existing["projection_digest"] != digest
                        or existing["receipt_id"] != receipt_id
                    ):
                        raise DerivedResearchReceiptError(
                            "conflicting source-bound derived receipt replay"
                        )
                    conn.execute("COMMIT")
                    return DerivedResearchReceipt(
                        session_id, topic, ordinal, source_record_id,
                        source_available_at_ms, receipt_id, True,
                    )
                row = conn.execute("""
                    SELECT last_ordinal FROM offline_research_derived_cursors
                    WHERE session_id=? AND consumer_topic=?
                """, (session_id, topic)).fetchone()
                expected = 1 if row is None else row["last_ordinal"] + 1
                if ordinal != expected:
                    raise DerivedResearchReceiptError(
                        "derived consumer ordinal gap or unverified reorder"
                    )
                conn.execute("""
                    INSERT INTO offline_research_derived_receipts(
                        session_id,consumer_topic,ordinal,source_record_id,
                        source_available_at_ms,projection_json,
                        projection_digest,receipt_id
                    ) VALUES (?,?,?,?,?,?,?,?)
                """, (session_id, topic, ordinal, source_record_id,
                      source_available_at_ms, serial, digest, receipt_id))
                conn.execute("""
                    INSERT INTO offline_research_derived_cursors(
                        session_id,consumer_topic,last_ordinal
                    ) VALUES (?,?,?)
                    ON CONFLICT(session_id,consumer_topic)
                    DO UPDATE SET last_ordinal=excluded.last_ordinal
                """, (session_id, topic, ordinal))
                if inject_crash_before_commit:
                    raise DerivedResearchReceiptError(
                        "injected crash before derived receipt and cursor COMMIT"
                    )
                conn.execute("COMMIT")
                return DerivedResearchReceipt(
                    session_id, topic, ordinal, source_record_id,
                    source_available_at_ms, receipt_id, False,
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def verified_count(self, session_id: str, topic: str) -> int:
        """Revalidate complete ordinal chain and SHA256 persisted projections."""
        self._simulation_only()
        if topic not in _TOPICS or type(session_id) is not str:
            raise DerivedResearchReceiptError("invalid derived receipt lookup")
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM offline_research_derived_receipts
                WHERE session_id=? AND consumer_topic=?
                ORDER BY ordinal
            """, (session_id, topic)).fetchall()
            cursor = conn.execute("""
                SELECT last_ordinal FROM offline_research_derived_cursors
                WHERE session_id=? AND consumer_topic=?
            """, (session_id, topic)).fetchone()
        for idx, row in enumerate(rows, start=1):
            if row["ordinal"] != idx:
                raise DerivedResearchReceiptError("derived receipt sequence gap")
            try:
                payload = json.loads(
                    row["projection_json"],
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
                    object_pairs_hook=_reject_duplicate_keys,
                )
            except (ValueError, TypeError) as exc:
                raise DerivedResearchReceiptError("malformed durable derived payload") from exc
            body, digest, receipt = self._validate(
                topic, session_id, idx, row["source_record_id"],
                row["source_available_at_ms"], payload,
            )
            if (
                body != row["projection_json"] or digest != row["projection_digest"]
                or receipt != row["receipt_id"]
            ):
                raise DerivedResearchReceiptError("persisted derived receipt corruption")
        latest = 0 if cursor is None else cursor["last_ordinal"]
        if len(rows) != latest:
            raise DerivedResearchReceiptError("derived durable cursor disagrees with receipts")
        return len(rows)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate derived JSON key")
        result[key] = value
    return result
