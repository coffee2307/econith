"""Opt-in cross-process durable projection transfer; separate SQLite databases.

A durable producer outbox already exists in offline_market_projections. The
receiver owns a SECOND SQLite database and atomically persists its local
result/receipt. If sender dies after receiver commit but before sender ACK,
retransmission is safe: receiver never re-applies an identical delivery.
There is NO network listener, broker ACK, external exactly-once effect,
authentication protocol, strategy execution or capital authorization here.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from core.mode import QuantMode
from infrastructure.market_projection_inbox import (
    OfflineMarketProjectionInbox, ProjectionDeliveryClaim,
)
from infrastructure.market_delivery_cursor import (
    DeliveryConflict, OfflineMarketProjectionCursor,
)


class CrossProcessDeliveryError(DeliveryConflict):
    """The receiver cannot safely trust, write or acknowledge the delivery."""


@dataclass(frozen=True)
class ProjectionEnvelope:
    source_consumer: str
    receiver: str
    ingest_id: int
    event_id: str
    available_at_ms: int
    projection_json: str
    projection_fingerprint: str


@dataclass(frozen=True)
class ReceiverOwnedReceipt:
    source_consumer: str
    receiver: str
    ingest_id: int
    event_id: str
    received_at_ms: int
    projection_fingerprint: str
    receipt_id: str

    def acknowledgement(self) -> dict[str, str]:
        """Deterministic ACK body; cannot be confused with broker ACK."""
        return {"receiver_receipt_id": self.receipt_id}


class OfflineIndependentProjectionReceiver:
    """Dedicated receiver-owned SQLite receipt ledger, openable by OS process.

    This ledger has a distinct path from the sender's journal; it never
    acquires a source DB connection or modifies a producer projection. Only
    immutable, canonical, bounded projections are admitted. No arbitrary
    user callback or external operation is performed in the ACK transaction.
    """

    def __init__(
        self, path: str | Path, *,
        mode_provider: Callable[[], QuantMode],
        max_projection_bytes: int = 65536,
        reuse_connection: bool = False,
    ) -> None:
        if not callable(mode_provider):
            raise ValueError("SIMULATION-only provider required")
        if type(max_projection_bytes) is not int or not 128 <= max_projection_bytes <= 4_194_304:
            raise ValueError("invalid receiver payload size bound")
        if type(reuse_connection) is not bool:
            raise ValueError("receiver connection reuse must be explicit boolean")
        self._reuse_connection = reuse_connection
        self._session_connection: sqlite3.Connection | None = None
        self.path = Path(path)
        self.mode_provider = mode_provider
        self.max_projection_bytes = max_projection_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_independent_projection_receipts (
                    source_consumer TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    ingest_id INTEGER NOT NULL CHECK (ingest_id > 0),
                    event_id TEXT NOT NULL,
                    available_at_ms INTEGER NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    projection_json TEXT NOT NULL,
                    projection_fingerprint TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    PRIMARY KEY(source_consumer,receiver,ingest_id)
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._reuse_connection:
            if self._session_connection is None:
                self._session_connection=sqlite3.connect(
                    self.path,isolation_level=None,timeout=10,
                )
                self._session_connection.row_factory=sqlite3.Row
                self._session_connection.execute("PRAGMA busy_timeout=10000")
            if self._session_connection.in_transaction:
                raise CrossProcessDeliveryError("unfinished receiver transaction")
            yield self._session_connection
        else:
            conn=sqlite3.connect(self.path,isolation_level=None,timeout=10)
            try:
                conn.row_factory=sqlite3.Row
                conn.execute("PRAGMA busy_timeout=10000")
                yield conn
            finally:
                conn.close()

    def close(self) -> None:
        db=self._session_connection
        if db is None:
            return
        self._session_connection=None
        try:
            if db.in_transaction:
                db.rollback()
                raise CrossProcessDeliveryError("receiver connection closed with uncommitted work")
        finally:
            db.close()

    def _simulation_only(self) -> None:
        if self.mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("receiver-owned market ACK is SIMULATION-only")

    @staticmethod
    def _validate_envelope(envelope: ProjectionEnvelope, now_ms: int, max_bytes: int) -> str:
        if not isinstance(envelope, ProjectionEnvelope):
            raise CrossProcessDeliveryError("typed projection envelope required")
        OfflineMarketProjectionCursor._validate_consumer(envelope.source_consumer)
        OfflineMarketProjectionCursor._validate_consumer(envelope.receiver)
        if type(envelope.ingest_id) is not int or envelope.ingest_id <= 0:
            raise CrossProcessDeliveryError("invalid source ingest ID")
        if not isinstance(envelope.event_id, str) or not envelope.event_id.startswith("md:") or len(envelope.event_id) > 240:
            raise CrossProcessDeliveryError("invalid market source event ID")
        if type(envelope.available_at_ms) is not int or not 0 < envelope.available_at_ms <= now_ms:
            raise CrossProcessDeliveryError("future or invalid source availability")
        if not isinstance(envelope.projection_json, str):
            raise CrossProcessDeliveryError("projection must be canonical JSON text")
        if len(envelope.projection_json.encode("utf-8")) > max_bytes:
            raise CrossProcessDeliveryError("projection payload exceeds size budget")
        try:
            data = json.loads(
                envelope.projection_json,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")),
                object_pairs_hook=_reject_duplicate_pairs,
            )
            serial, digest = OfflineMarketProjectionCursor._serialize(data)
        except (TypeError, ValueError) as exc:
            raise CrossProcessDeliveryError("malformed canonical projection body") from exc
        if (
            envelope.projection_json != serial
            or digest != envelope.projection_fingerprint
        ):
            raise CrossProcessDeliveryError("projection fingerprint mismatch")
        return digest

    @staticmethod
    def _receipt_id(e: ProjectionEnvelope) -> str:
        canonical = json.dumps(
            [e.source_consumer, e.receiver, e.ingest_id, e.event_id,
             e.available_at_ms, e.projection_fingerprint],
            ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _row(row: sqlite3.Row) -> ReceiverOwnedReceipt:
        return ReceiverOwnedReceipt(
            row["source_consumer"], row["receiver"], row["ingest_id"],
            row["event_id"], row["received_at_ms"],
            row["projection_fingerprint"], row["receipt_id"],
        )

    def accept(
        self, envelope: ProjectionEnvelope, *, now_ms: int | None = None,
    ) -> ReceiverOwnedReceipt:
        """Atomic receiver-owned receipt. Identical retries do no work."""
        self._simulation_only()
        now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        OfflineMarketProjectionCursor._positive_ms(now, "now_ms")
        self._validate_envelope(envelope, now, self.max_projection_bytes)
        fingerprint = self._receipt_id(envelope)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute("""
                    SELECT * FROM offline_independent_projection_receipts
                    WHERE source_consumer=? AND receiver=? AND ingest_id=?
                """, (envelope.source_consumer, envelope.receiver,
                      envelope.ingest_id)).fetchone()
                if row is not None:
                    if (
                        row["event_id"] != envelope.event_id
                        or row["available_at_ms"] != envelope.available_at_ms
                        or row["projection_json"] != envelope.projection_json
                        or row["projection_fingerprint"] != envelope.projection_fingerprint
                        or row["receipt_id"] != fingerprint
                    ):
                        raise CrossProcessDeliveryError("conflicting cross-process replay")
                    receipt = self._row(row)
                    conn.execute("COMMIT")
                    return receipt
                conn.execute("""
                    INSERT INTO offline_independent_projection_receipts (
                        source_consumer,receiver,ingest_id,event_id,
                        available_at_ms,received_at_ms,projection_json,
                        projection_fingerprint,receipt_id
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                """, (envelope.source_consumer, envelope.receiver,
                      envelope.ingest_id, envelope.event_id,
                      envelope.available_at_ms, now, envelope.projection_json,
                      envelope.projection_fingerprint, fingerprint))
                conn.execute("COMMIT")
                return ReceiverOwnedReceipt(
                    envelope.source_consumer, envelope.receiver,
                    envelope.ingest_id, envelope.event_id, now,
                    envelope.projection_fingerprint, fingerprint,
                )
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def receipts(self, source_consumer: str, receiver: str) -> list[ReceiverOwnedReceipt]:
        self._simulation_only()
        OfflineMarketProjectionCursor._validate_consumer(source_consumer)
        OfflineMarketProjectionCursor._validate_consumer(receiver)
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM offline_independent_projection_receipts
                WHERE source_consumer=? AND receiver=?
                ORDER BY ingest_id
            """, (source_consumer, receiver)).fetchall()
            out = []
            for row in rows:
                envelope = ProjectionEnvelope(
                    row["source_consumer"], row["receiver"], row["ingest_id"],
                    row["event_id"], row["available_at_ms"],
                    row["projection_json"], row["projection_fingerprint"],
                )
                self._validate_envelope(envelope, row["received_at_ms"],
                                        self.max_projection_bytes)
                if self._receipt_id(envelope) != row["receipt_id"]:
                    raise CrossProcessDeliveryError("corrupted durable receiver receipt")
                out.append(self._row(row))
            return out


def _reject_duplicate_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for key, value in items:
        if key in data:
            raise ValueError("duplicate JSON projection key")
        data[key] = value
    return data


class OfflineCrossProcessProjectionSender:
    """Local bridge protocol, usable through a separately hosted receiver.

    The call to receiver_accept may cross an RPC/process boundary, but this
    module itself opens no sockets or HTTP endpoints. The receiver's durable
    receipt (not a transient queue ACK) is a prerequisite for producer ACK.
    """

    def __init__(self, source_inbox: OfflineMarketProjectionInbox) -> None:
        if not isinstance(source_inbox, OfflineMarketProjectionInbox):
            raise ValueError("durable source projection inbox required")
        source_inbox.journal._simulation_only()
        self.source_inbox = source_inbox

    @staticmethod
    def envelope(claim: ProjectionDeliveryClaim) -> ProjectionEnvelope:
        if not isinstance(claim, ProjectionDeliveryClaim):
            raise ValueError("valid source claim required")
        serial, digest = OfflineMarketProjectionCursor._serialize(
            claim.projection.value,
        )
        if digest != claim.projection_fingerprint:
            raise CrossProcessDeliveryError("source claim projection corrupted")
        return ProjectionEnvelope(
            claim.source_consumer, claim.receiver, claim.ingest_id,
            claim.projection.event_id, claim.projection.available_at_ms,
            serial, digest,
        )

    def deliver_once(
        self, source_consumer: str, receiver: str, *,
        receiver_accept: Callable[[ProjectionEnvelope], ReceiverOwnedReceipt],
        now_ms: int,
        lease_ms: int = 30_000,
        fail_after_receiver_commit: bool = False,
    ) -> ReceiverOwnedReceipt | None:
        self.source_inbox.journal._simulation_only()
        if not callable(receiver_accept):
            raise ValueError("receiver callback required")
        claim = self.source_inbox.claim_next(
            source_consumer, receiver, now_ms=now_ms, lease_ms=lease_ms,
        )
        if claim is None:
            return None
        envelope = self.envelope(claim)
        result = receiver_accept(envelope)
        if (
            not isinstance(result, ReceiverOwnedReceipt)
            or result.source_consumer != envelope.source_consumer
            or result.receiver != envelope.receiver
            or result.ingest_id != envelope.ingest_id
            or result.event_id != envelope.event_id
            or result.projection_fingerprint != envelope.projection_fingerprint
            or result.receipt_id != OfflineIndependentProjectionReceiver._receipt_id(envelope)
            or result.received_at_ms < envelope.available_at_ms
        ):
            raise CrossProcessDeliveryError("receiver returned mismatched durable receipt")
        if fail_after_receiver_commit:
            raise CrossProcessDeliveryError(
                "injected sender crash after receiver commit before sender ACK"
            )
        self.source_inbox.acknowledge(
            claim, result.acknowledgement(), now_ms=now_ms,
        )
        return result
