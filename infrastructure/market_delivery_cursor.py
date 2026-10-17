"""Opt-in, SIMULATION-only durable projection consumer for validated market ticks.

A queue handoff alone is not processing acknowledgement. This class grants
single-consumer leases and advances a durable cursor ONLY in the SAME SQLite
transaction that persists a deterministic projection. It never publishes to
EventBus, controls a broker or authorizes capital. An external irreversible
side effect must not be performed inside the projection step: an expired lease
can be replayed with a fresh token and thus external effects would be at-least-
once unless independently idempotent.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Mapping

from infrastructure.market_data_integrity import (
    MarketEventError, OfflineMarketIntegrityJournal, StoredMarketEvent,
)

_CONSUMER = re.compile(r"^[A-Za-z0-9_-]{1,48}$")


class DeliveryConflict(ValueError):
    """Consumer lease, cursor or projection precondition violated."""


@dataclass(frozen=True)
class DeliveryClaim:
    consumer: str
    ingest_id: int
    event: StoredMarketEvent
    token: str
    expires_at_ms: int


@dataclass(frozen=True)
class DurableProjection:
    consumer: str
    ingest_id: int
    event_id: str
    available_at_ms: int
    value: dict[str, Any]


class OfflineMarketProjectionCursor:
    """Per-consumer, lease-fenced cursor over immutable market source journal.

    Each projection is stored atomically with the cursor. A killed process before
    COMMIT cannot create a permanently acknowledged-but-unprojected event.
    The cursor follows database append order, NOT exchange timestamp order.
    This is a local SQLite projection contract, not delivery to EventBus.
    """

    def __init__(self, journal: OfflineMarketIntegrityJournal) -> None:
        self.journal = journal
        self.journal._simulation_only()
        with self.journal._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_delivery_cursors (
                    consumer TEXT PRIMARY KEY,
                    last_ingest_id INTEGER NOT NULL CHECK(last_ingest_id >= 0)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_delivery_claims (
                    consumer TEXT PRIMARY KEY,
                    ingest_id INTEGER NOT NULL,
                    claim_token TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    FOREIGN KEY (ingest_id) REFERENCES offline_market_events(ingest_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_projections (
                    consumer TEXT NOT NULL,
                    ingest_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    available_at_ms INTEGER NOT NULL,
                    value_json TEXT NOT NULL,
                    value_fingerprint TEXT NOT NULL,
                    PRIMARY KEY(consumer, ingest_id),
                    FOREIGN KEY (ingest_id) REFERENCES offline_market_events(ingest_id)
                )
            """)

    @staticmethod
    def _validate_consumer(consumer: str) -> None:
        if not isinstance(consumer, str) or not _CONSUMER.fullmatch(consumer):
            raise ValueError("consumer must be a bounded stable identifier")

    @staticmethod
    def _positive_ms(value: int, field: str) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{field} must be a positive integer")

    @staticmethod
    def _serialize(value: Mapping[str, Any]) -> tuple[str, str]:
        if not isinstance(value, Mapping):
            raise ValueError("projection must be a JSON mapping")
        try:
            serialized = json.dumps(
                dict(value), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("projection must be finite JSON") from exc
        return serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _now(override: int | None) -> int:
        now = int(time.time() * 1000) if override is None else override
        OfflineMarketProjectionCursor._positive_ms(now, "now_ms")
        return now

    @staticmethod
    def _cursor(conn: sqlite3.Connection, consumer: str) -> int:
        row = conn.execute(
            "SELECT last_ingest_id FROM offline_market_delivery_cursors WHERE consumer=?",
            (consumer,),
        ).fetchone()
        return 0 if row is None else row["last_ingest_id"]

    def claim_next(
        self,
        consumer: str,
        *,
        now_ms: int | None = None,
        as_of_ms: int | None = None,
        lease_ms: int = 30_000,
    ) -> DeliveryClaim | None:
        """Claim the earliest unprojected and observable event.

        An unexpired lease blocks the next claim for the SAME consumer.
        Another consumer is independent. On expiry a new random claim token
        fences any stale worker, without skipping an uncommitted event.
        """
        self.journal._simulation_only()
        self._validate_consumer(consumer)
        now = self._now(now_ms)
        cutoff = now if as_of_ms is None else as_of_ms
        self._positive_ms(cutoff, "as_of_ms")
        if (
            not isinstance(lease_ms, int) or isinstance(lease_ms, bool)
            or not 1 <= lease_ms <= 300_000
        ):
            raise ValueError("lease_ms must be between 1 and 300000")
        if cutoff > now:
            raise ValueError("as_of must not exceed local processing time")
        with self.journal._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                last_ingest_id = self._cursor(conn, consumer)
                lease = conn.execute(
                    "SELECT * FROM offline_market_delivery_claims WHERE consumer=?",
                    (consumer,),
                ).fetchone()
                if lease is not None and lease["expires_at_ms"] > now:
                    conn.execute("COMMIT")
                    return None
                row = conn.execute(
                    """SELECT * FROM offline_market_events
                       WHERE ingest_id > ? ORDER BY ingest_id LIMIT 1""",
                    (last_ingest_id,),
                ).fetchone()
                if row is None or row["available_at_ms"] > cutoff:
                    conn.execute("COMMIT")
                    return None
                event = self.journal._verified_event(row)
                token = secrets.token_hex(24)
                expires = now + lease_ms
                conn.execute(
                    """INSERT INTO offline_market_delivery_claims
                       (consumer,ingest_id,claim_token,expires_at_ms)
                       VALUES (?,?,?,?)
                       ON CONFLICT(consumer) DO UPDATE SET
                         ingest_id=excluded.ingest_id,
                         claim_token=excluded.claim_token,
                         expires_at_ms=excluded.expires_at_ms""",
                    (consumer, row["ingest_id"], token, expires),
                )
                conn.execute("COMMIT")
                return DeliveryClaim(
                    consumer=consumer, ingest_id=row["ingest_id"],
                    event=event, token=token, expires_at_ms=expires,
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def commit_projection(
        self,
        claim: DeliveryClaim,
        projection: Mapping[str, Any],
        *,
        now_ms: int | None = None,
    ) -> DurableProjection:
        """Write a projection + cursor together, fenced by the current lease.

        No EventBus publish occurs here: emitting to EventBus after this
        transaction is a SEPARATE, as-yet-unresolved outbox/inbox problem.
        """
        self.journal._simulation_only()
        if not isinstance(claim, DeliveryClaim):
            raise DeliveryConflict("invalid delivery claim")
        self._validate_consumer(claim.consumer)
        serialized, digest = self._serialize(projection)
        now = self._now(now_ms)
        with self.journal._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                stored_claim = conn.execute(
                    "SELECT * FROM offline_market_delivery_claims WHERE consumer=?",
                    (claim.consumer,),
                ).fetchone()
                if (
                    stored_claim is None
                    or stored_claim["claim_token"] != claim.token
                    or stored_claim["ingest_id"] != claim.ingest_id
                    or stored_claim["expires_at_ms"] != claim.expires_at_ms
                    or now >= stored_claim["expires_at_ms"]
                ):
                    raise DeliveryConflict("delivery lease missing, expired or superseded")
                cursor = self._cursor(conn, claim.consumer)
                row = conn.execute(
                    """SELECT * FROM offline_market_events
                       WHERE ingest_id > ? ORDER BY ingest_id LIMIT 1""",
                    (cursor,),
                ).fetchone()
                if row is None or row["ingest_id"] != claim.ingest_id:
                    raise DeliveryConflict("projection would skip or repeat cursor position")
                verified = self.journal._verified_event(row)
                if (
                    verified != claim.event or row["available_at_ms"] > now
                ):
                    raise DeliveryConflict("claimed market event changed or is not observable")
                conn.execute(
                    """INSERT INTO offline_market_projections
                       (consumer,ingest_id,event_id,available_at_ms,
                        value_json,value_fingerprint)
                       VALUES (?,?,?,?,?,?)""",
                    (claim.consumer, claim.ingest_id, verified.event_id,
                     verified.available_at_ms, serialized, digest),
                )
                conn.execute(
                    """INSERT INTO offline_market_delivery_cursors
                       (consumer,last_ingest_id) VALUES (?,?)
                       ON CONFLICT(consumer) DO UPDATE
                       SET last_ingest_id=excluded.last_ingest_id""",
                    (claim.consumer, claim.ingest_id),
                )
                conn.execute(
                    "DELETE FROM offline_market_delivery_claims WHERE consumer=?",
                    (claim.consumer,),
                )
                conn.execute("COMMIT")
                return DurableProjection(
                    consumer=claim.consumer, ingest_id=claim.ingest_id,
                    event_id=verified.event_id,
                    available_at_ms=verified.available_at_ms,
                    value=json.loads(serialized),
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def projections(
        self, consumer: str, *, as_of_ms: int | None = None,
    ) -> list[DurableProjection]:
        """Read and verify durable projections; never fabricate bus acknowledgements."""
        self.journal._simulation_only()
        self._validate_consumer(consumer)
        if as_of_ms is not None:
            self._positive_ms(as_of_ms, "as_of_ms")
        with self.journal._connect() as conn:
            rows = conn.execute(
                """SELECT p.*, e.frame_json, e.fingerprint, e.scope, e.sequence,
                          e.event_ms, e.trade_ms
                   FROM offline_market_projections p
                   JOIN offline_market_events e ON p.ingest_id=e.ingest_id
                   WHERE p.consumer=?
                     AND (? IS NULL OR p.available_at_ms <= ?)
                   ORDER BY p.ingest_id""",
                (consumer, as_of_ms, as_of_ms),
            ).fetchall()
        result = []
        for row in rows:
            source = self.journal._verified_event(row)
            try:
                value = json.loads(row["value_json"])
                serialized, digest = self._serialize(value)
            except (TypeError, ValueError) as exc:
                raise MarketEventError("stored projection is malformed") from exc
            if (
                row["event_id"] != source.event_id
                or row["available_at_ms"] != source.available_at_ms
                or serialized != row["value_json"]
                or digest != row["value_fingerprint"]
            ):
                raise MarketEventError("stored market projection failed integrity verification")
            result.append(DurableProjection(
                consumer=consumer, ingest_id=row["ingest_id"],
                event_id=source.event_id,
                available_at_ms=source.available_at_ms,
                value=value,
            ))
        return result

    def cursor_position(self, consumer: str) -> int:
        """Introspection only: source journal and consumer progress differ."""
        self.journal._simulation_only()
        self._validate_consumer(consumer)
        with self.journal._connect() as conn:
            return self._cursor(conn, consumer)
