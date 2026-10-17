"""SIMULATION-only durable local projection outbox + idempotent SQLite inbox.

The outbox is the existing immutable offline_market_projections table, which is
persisted *with* the producer cursor. It is not a transient EventBus queue.
Consumers lease the earliest unacknowledged projection. A receipt is a fully
persisted local JSON result in the SAME SQLite database, committed together
with idempotency evidence. There is no broker, network or external ACK.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from infrastructure.market_data_integrity import MarketEventError
from infrastructure.market_delivery_cursor import (
    DeliveryConflict, DurableProjection, OfflineMarketProjectionCursor,
)


@dataclass(frozen=True)
class ProjectionDeliveryClaim:
    source_consumer: str
    receiver: str
    ingest_id: int
    projection: DurableProjection
    projection_fingerprint: str
    token: str
    expires_at_ms: int


@dataclass(frozen=True)
class DurableInboxReceipt:
    source_consumer: str
    receiver: str
    ingest_id: int
    event_id: str
    source_available_at_ms: int
    committed_at_ms: int
    result: dict[str, Any]


class OfflineMarketProjectionInbox:
    """Opt-in SQLite-local durable outbox consumption.

    An uncommitted callback can be invoked again after a process failure.
    Only pure deterministic computation is permitted before acknowledge().
    An effect in another process, database, EventBus or venue is NOT made
    exactly-once by this module. Store any local result in receipt.result.
    """

    def __init__(self, producer: OfflineMarketProjectionCursor) -> None:
        if not isinstance(producer, OfflineMarketProjectionCursor):
            raise ValueError("a verified local projection cursor is required")
        self.producer = producer
        self.journal = producer.journal
        self.journal._simulation_only()
        with self.journal._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_projection_inbox_claims (
                    source_consumer TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    ingest_id INTEGER NOT NULL,
                    token TEXT NOT NULL,
                    expires_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (source_consumer, receiver),
                    FOREIGN KEY (source_consumer, ingest_id)
                        REFERENCES offline_market_projections (consumer, ingest_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_projection_inbox_receipts (
                    source_consumer TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    ingest_id INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    source_available_at_ms INTEGER NOT NULL,
                    projection_fingerprint TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_fingerprint TEXT NOT NULL,
                    claim_token TEXT NOT NULL,
                    committed_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (source_consumer, receiver, ingest_id),
                    FOREIGN KEY (source_consumer, ingest_id)
                        REFERENCES offline_market_projections (consumer, ingest_id)
                )
            """)

    def _validate_pair(self, source_consumer: str, receiver: str) -> None:
        self.producer._validate_consumer(source_consumer)
        self.producer._validate_consumer(receiver)

    def _load_projection(
        self, conn: sqlite3.Connection, source_consumer: str, ingest_id: int,
    ) -> tuple[DurableProjection, str]:
        row = conn.execute("""
            SELECT p.consumer, p.ingest_id,
                   p.event_id AS projection_event_id,
                   p.available_at_ms AS projection_available_at_ms,
                   p.value_json, p.value_fingerprint,
                   e.event_id, e.available_at_ms, e.frame_json, e.fingerprint,
                   e.scope, e.sequence, e.event_ms, e.trade_ms
            FROM offline_market_projections p
            JOIN offline_market_events e ON p.ingest_id=e.ingest_id
            WHERE p.consumer=? AND p.ingest_id=?
        """, (source_consumer, ingest_id)).fetchone()
        if row is None:
            raise DeliveryConflict("projection is absent or source is missing")
        source = self.journal._verified_event(row)
        try:
            payload = json.loads(row["value_json"])
            serial, digest = self.producer._serialize(payload)
        except (TypeError, ValueError) as exc:
            raise MarketEventError("stored projection has invalid JSON") from exc
        if (
            serial != row["value_json"]
            or digest != row["value_fingerprint"]
            or row["projection_event_id"] != source.event_id
            or row["projection_available_at_ms"] != source.available_at_ms
            or self.producer._cursor(conn, source_consumer) < ingest_id
        ):
            raise MarketEventError("stored projection or cursor failed verification")
        return DurableProjection(
            source_consumer, ingest_id, source.event_id,
            source.available_at_ms, payload,
        ), digest

    @staticmethod
    def _first_pending(
        conn: sqlite3.Connection, source_consumer: str, receiver: str,
    ) -> sqlite3.Row | None:
        return conn.execute("""
            SELECT p.ingest_id, p.available_at_ms
            FROM offline_market_projections p
            WHERE p.consumer=?
              AND NOT EXISTS (
                SELECT 1 FROM offline_market_projection_inbox_receipts r
                WHERE r.source_consumer=p.consumer AND r.ingest_id=p.ingest_id
                  AND r.receiver=?
              )
            ORDER BY p.ingest_id LIMIT 1
        """, (source_consumer, receiver)).fetchone()

    def claim_next(
        self, source_consumer: str, receiver: str, *,
        now_ms: int | None = None, as_of_ms: int | None = None,
        lease_ms: int = 30_000,
    ) -> ProjectionDeliveryClaim | None:
        """Lease earliest unacknowledged durable projection; never skip by as-of."""
        self.journal._simulation_only()
        self._validate_pair(source_consumer, receiver)
        now = self.producer._now(now_ms)
        cutoff = now if as_of_ms is None else as_of_ms
        self.producer._positive_ms(cutoff, "as_of_ms")
        if cutoff > now:
            raise ValueError("as_of_ms must not exceed processing time")
        if (
            not isinstance(lease_ms, int) or isinstance(lease_ms, bool)
            or not 1 <= lease_ms <= 300_000
        ):
            raise ValueError("lease_ms must be between 1 and 300000")
        with self.journal._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                active = conn.execute("""
                    SELECT * FROM offline_market_projection_inbox_claims
                    WHERE source_consumer=? AND receiver=?
                """, (source_consumer, receiver)).fetchone()
                if active is not None and active["expires_at_ms"] > now:
                    conn.execute("COMMIT")
                    return None
                pending = self._first_pending(conn, source_consumer, receiver)
                if pending is None or pending["available_at_ms"] > cutoff:
                    conn.execute("COMMIT")
                    return None
                projection, digest = self._load_projection(
                    conn, source_consumer, pending["ingest_id"],
                )
                token = secrets.token_hex(24)
                expires = now + lease_ms
                conn.execute("""
                    INSERT INTO offline_market_projection_inbox_claims
                        (source_consumer,receiver,ingest_id,token,expires_at_ms)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(source_consumer,receiver) DO UPDATE SET
                      ingest_id=excluded.ingest_id,
                      token=excluded.token,
                      expires_at_ms=excluded.expires_at_ms
                """, (source_consumer, receiver, pending["ingest_id"],
                      token, expires))
                conn.execute("COMMIT")
                return ProjectionDeliveryClaim(
                    source_consumer, receiver, pending["ingest_id"],
                    projection, digest, token, expires,
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _verified_receipt(
        self, conn: sqlite3.Connection, row: sqlite3.Row,
    ) -> DurableInboxReceipt:
        projected, fingerprint = self._load_projection(
            conn, row["source_consumer"], row["ingest_id"],
        )
        try:
            value = json.loads(row["result_json"])
            serial, digest = self.producer._serialize(value)
        except (ValueError, TypeError) as exc:
            raise MarketEventError("stored inbox result is malformed") from exc
        if (
            serial != row["result_json"]
            or digest != row["result_fingerprint"]
            or fingerprint != row["projection_fingerprint"]
            or row["event_id"] != projected.event_id
            or row["source_available_at_ms"] != projected.available_at_ms
            or row["committed_at_ms"] < projected.available_at_ms
        ):
            raise MarketEventError("stored inbox receipt failed verification")
        return DurableInboxReceipt(
            row["source_consumer"], row["receiver"], row["ingest_id"],
            projected.event_id, projected.available_at_ms,
            row["committed_at_ms"], value,
        )

    def acknowledge(
        self, claim: ProjectionDeliveryClaim, result: Mapping[str, Any], *,
        now_ms: int | None = None,
    ) -> DurableInboxReceipt:
        """Atomic local result + receipt. Idempotent for identical token/payload.

        Callback/external side effects BEFORE this operation are unsafe and
        may be repeated after crash. Do not confuse this with EventBus ACK.
        """
        self.journal._simulation_only()
        if not isinstance(claim, ProjectionDeliveryClaim):
            raise DeliveryConflict("invalid inbox delivery claim")
        self._validate_pair(claim.source_consumer, claim.receiver)
        serial, digest = self.producer._serialize(result)
        now = self.producer._now(now_ms)
        with self.journal._connect() as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                stored = conn.execute("""
                    SELECT * FROM offline_market_projection_inbox_receipts
                    WHERE source_consumer=? AND receiver=? AND ingest_id=?
                """, (claim.source_consumer, claim.receiver,
                      claim.ingest_id)).fetchone()
                if stored is not None:
                    verified = self._verified_receipt(conn, stored)
                    if (
                        stored["claim_token"] != claim.token
                        or stored["result_json"] != serial
                        or stored["result_fingerprint"] != digest
                        or stored["projection_fingerprint"] != claim.projection_fingerprint
                        or verified.event_id != claim.projection.event_id
                    ):
                        raise DeliveryConflict("conflicting duplicate inbox acknowledgment")
                    conn.execute("COMMIT")
                    return verified
                active = conn.execute("""
                    SELECT * FROM offline_market_projection_inbox_claims
                    WHERE source_consumer=? AND receiver=?
                """, (claim.source_consumer, claim.receiver)).fetchone()
                if (
                    active is None
                    or active["ingest_id"] != claim.ingest_id
                    or active["token"] != claim.token
                    or active["expires_at_ms"] != claim.expires_at_ms
                    or now >= active["expires_at_ms"]
                ):
                    raise DeliveryConflict("inbox lease is expired or superseded")
                pending = self._first_pending(
                    conn, claim.source_consumer, claim.receiver,
                )
                if pending is None or pending["ingest_id"] != claim.ingest_id:
                    raise DeliveryConflict("inbox acknowledgment would skip projection")
                projection, fingerprint = self._load_projection(
                    conn, claim.source_consumer, claim.ingest_id,
                )
                if (
                    projection != claim.projection
                    or fingerprint != claim.projection_fingerprint
                    or projection.available_at_ms > now
                ):
                    raise DeliveryConflict("claimed projection changed or is unavailable")
                conn.execute("""
                    INSERT INTO offline_market_projection_inbox_receipts
                    (source_consumer,receiver,ingest_id,event_id,
                     source_available_at_ms,projection_fingerprint,
                     result_json,result_fingerprint,claim_token,committed_at_ms)
                    VALUES (?,?,?,?,?,?,?,?,?,?)
                """, (claim.source_consumer, claim.receiver, claim.ingest_id,
                      projection.event_id, projection.available_at_ms,
                      fingerprint, serial, digest, claim.token, now))
                conn.execute("""
                    DELETE FROM offline_market_projection_inbox_claims
                    WHERE source_consumer=? AND receiver=?
                """, (claim.source_consumer, claim.receiver))
                conn.execute("COMMIT")
                return DurableInboxReceipt(
                    claim.source_consumer, claim.receiver, claim.ingest_id,
                    projection.event_id, projection.available_at_ms, now,
                    json.loads(serial),
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def receipts(
        self, source_consumer: str, receiver: str, *,
        as_of_ms: int | None = None,
    ) -> list[DurableInboxReceipt]:
        """Read verified immutable local results as-of committed receipt time."""
        self.journal._simulation_only()
        self._validate_pair(source_consumer, receiver)
        if as_of_ms is not None:
            self.producer._positive_ms(as_of_ms, "as_of_ms")
        with self.journal._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM offline_market_projection_inbox_receipts
                WHERE source_consumer=? AND receiver=?
                  AND (? IS NULL OR committed_at_ms<=?)
                ORDER BY ingest_id
            """, (source_consumer, receiver, as_of_ms, as_of_ms)).fetchall()
            return [self._verified_receipt(conn, row) for row in rows]
