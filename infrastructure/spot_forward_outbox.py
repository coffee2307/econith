"""Durable SIMULATION-only forward research producer outbox.

Three monotonic states: STAGED -> RECEIVER_ACKED -> DERIVED_ACKED.
One SQLite transaction per state update, WAL/FULL. Exact immutable receiver
envelope is STAGED *before* any IPC. The receiver's separate SQLite COMMIT
may happen before our ACK; replaying the identical envelope is idempotent.
No trading, execution, arbitrary subscribers or global exactly-once guarantee.

Because raw capture is committed BEFORE the optional callback, a hard exit
between raw COMMIT and this outbox STAGE can leave an uncovered original.
Recovery therefore only certifies staged projections, NEVER raw-source
completeness, and must quarantine the source session for full rescan.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    ProjectionEnvelope, ReceiverOwnedReceipt,
    OfflineIndependentProjectionReceiver,
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor

_STATES = ("STAGED", "RECEIVER_ACKED", "DERIVED_ACKED")


class ForwardOutboxError(ValueError):
    """Invalid durable research projection or state transition."""


@dataclass(frozen=True)
class ForwardOutboxItem:
    envelope: ProjectionEnvelope
    status: str
    receiver_receipt_id: str | None


class OfflineForwardSpotOutbox:
    """A single-session producer owned ledger; no other process writes it."""

    def __init__(
        self, path: Path, *, session_id: str,
        mode_provider: Callable[[], QuantMode],
    ) -> None:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or type(session_id) is not str or not session_id.startswith("rec_")
            or len(session_id) > 48
            or not callable(mode_provider)
            or mode_provider() is not QuantMode.SIMULATION
        ):
            raise ForwardOutboxError("invalid SIMULATION forward outbox identity")
        self.path = path.resolve()
        self.session_id = session_id
        self._mode_provider = mode_provider
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS forward_outbox_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    session_id TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS forward_outbox (
                    ingest_id INTEGER PRIMARY KEY CHECK(ingest_id > 0),
                    source_consumer TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    available_at_ms INTEGER NOT NULL,
                    projection_json TEXT NOT NULL,
                    projection_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(
                        status IN ('STAGED','RECEIVER_ACKED','DERIVED_ACKED')
                    ),
                    receiver_receipt_id TEXT,
                    CHECK(status='STAGED' OR receiver_receipt_id IS NOT NULL)
                )
            """)
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""
                    INSERT OR IGNORE INTO forward_outbox_identity
                    (singleton,session_id) VALUES (1,?)
                """, (session_id,))
                row = db.execute("""
                    SELECT session_id FROM forward_outbox_identity WHERE singleton=1
                """).fetchone()
                if row is None or row[0] != session_id:
                    raise ForwardOutboxError("outbox session identity changed")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

    def _simulation(self) -> None:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise ForwardOutboxError("outbox denied outside SIMULATION")

    def _validate(self, e: ProjectionEnvelope) -> None:
        self._simulation()
        if (
            not isinstance(e, ProjectionEnvelope)
            or e.source_consumer != "forward_spot"
            or e.receiver != "read_only_market"
            or type(e.ingest_id) is not int or e.ingest_id <= 0
            or e.event_id != f"md:forward:{self.session_id}:{e.ingest_id}"
            or type(e.available_at_ms) is not int or e.available_at_ms <= 0
        ):
            raise ForwardOutboxError("invalid scoped forward source envelope")
        try:
            import json
            body = json.loads(e.projection_json)
            serial, digest = OfflineMarketProjectionCursor._serialize(body)
        except (TypeError, ValueError) as exc:
            raise ForwardOutboxError("invalid canonical forward outbox projection") from exc
        if (
            len(serial.encode("utf-8")) > 65536
            or serial != e.projection_json or digest != e.projection_fingerprint
            or type(body) is not dict
            or body.get("session_id") != self.session_id
            or body.get("source_capture_id") != e.ingest_id
            or body.get("source_available_at_ms") != e.available_at_ms
            or body.get("source_record_id", "").split(":")[0] != self.session_id
            or body.get("topic") not in ("md.aggTrade", "md.depth")
        ):
            raise ForwardOutboxError("outbox source coordinates or projection mismatch")

    @staticmethod
    def _item(row: sqlite3.Row) -> ForwardOutboxItem:
        env = ProjectionEnvelope(
            row["source_consumer"], row["receiver"], row["ingest_id"],
            row["event_id"], row["available_at_ms"],
            row["projection_json"], row["projection_fingerprint"],
        )
        return ForwardOutboxItem(env, row["status"], row["receiver_receipt_id"])

    def stage(self, envelope: ProjectionEnvelope) -> ForwardOutboxItem:
        """Persist full immutable send intent before IPC; exact replay only."""
        self._validate(envelope)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM forward_outbox WHERE ingest_id=?",
                    (envelope.ingest_id,),
                ).fetchone()
                if row is None:
                    db.execute("""
                        INSERT INTO forward_outbox (
                            ingest_id,source_consumer,receiver,event_id,
                            available_at_ms,projection_json,projection_fingerprint,
                            status,receiver_receipt_id
                        ) VALUES (?,?,?,?,?,?,?,'STAGED',NULL)
                    """, (
                        envelope.ingest_id, envelope.source_consumer,
                        envelope.receiver, envelope.event_id,
                        envelope.available_at_ms, envelope.projection_json,
                        envelope.projection_fingerprint,
                    ))
                    row = db.execute(
                        "SELECT * FROM forward_outbox WHERE ingest_id=?",
                        (envelope.ingest_id,),
                    ).fetchone()
                item = self._item(row)
                if item.envelope != envelope:
                    raise ForwardOutboxError("conflicting immutable forward envelope")
                db.execute("COMMIT")
                return item
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def ack_receiver(self, envelope: ProjectionEnvelope, receipt: ReceiverOwnedReceipt) -> None:
        self._validate(envelope)
        expected = OfflineIndependentProjectionReceiver._receipt_id(envelope)
        if (
            not isinstance(receipt, ReceiverOwnedReceipt)
            or receipt.receipt_id != expected
            or receipt.ingest_id != envelope.ingest_id
            or receipt.event_id != envelope.event_id
            or receipt.source_consumer != envelope.source_consumer
            or receipt.receiver != envelope.receiver
            or receipt.projection_fingerprint != envelope.projection_fingerprint
        ):
            raise ForwardOutboxError("receiver-owned ACK does not match outbox envelope")
        self._advance(envelope, "RECEIVER_ACKED", expected)

    def ack_derived(self, envelope: ProjectionEnvelope) -> None:
        self._validate(envelope)
        self._advance(
            envelope, "DERIVED_ACKED",
            OfflineIndependentProjectionReceiver._receipt_id(envelope),
        )

    def _advance(self, env: ProjectionEnvelope, target: str, receipt_id: str) -> None:
        self._simulation()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute(
                    "SELECT * FROM forward_outbox WHERE ingest_id=?",
                    (env.ingest_id,),
                ).fetchone()
                if row is None:
                    raise ForwardOutboxError("cannot acknowledge unstaged forward envelope")
                item = self._item(row)
                if item.envelope != env or (
                    item.receiver_receipt_id is not None
                    and item.receiver_receipt_id != receipt_id
                ):
                    raise ForwardOutboxError("conflicting ACK or source envelope")
                if target == "DERIVED_ACKED" and item.status == "STAGED":
                    raise ForwardOutboxError("receiver ACK must precede derived ACK")
                if _STATES.index(target) > _STATES.index(item.status):
                    db.execute("""
                        UPDATE forward_outbox SET status=?,receiver_receipt_id=?
                        WHERE ingest_id=?
                    """, (target, receipt_id, env.ingest_id))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def completed_item(self, envelope: ProjectionEnvelope) -> ForwardOutboxItem:
        """Indexed proof of one exact producer/independent receiver ACK.

        The authorizing receiver ACK and derived subscriber commits happen
        before this read. Whole-prefix/source/observer proofs are still
        mandatory at sealed session close; this cannot replace them.
        """
        self._validate(envelope)
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM forward_outbox WHERE ingest_id=?",
                (envelope.ingest_id,),
            ).fetchone()
        if row is None:
            raise ForwardOutboxError("missing durable acknowledged event")
        item = self._item(row)
        expected = OfflineIndependentProjectionReceiver._receipt_id(envelope)
        if (item.envelope != envelope
            or item.status != "DERIVED_ACKED"
            or item.receiver_receipt_id != expected):
            raise ForwardOutboxError("event has no complete independently bound ACK")
        return item

    def items(self) -> list[ForwardOutboxItem]:
        self._simulation()
        with self._connect() as db:
            result = [self._item(row) for row in db.execute(
                "SELECT * FROM forward_outbox ORDER BY ingest_id"
            )]
        for entry in result:
            self._validate(entry.envelope)
            if entry.status not in _STATES or (
                entry.status != "STAGED"
                and entry.receiver_receipt_id !=
                OfflineIndependentProjectionReceiver._receipt_id(entry.envelope)
            ):
                raise ForwardOutboxError("persisted state/receipt corrupt")
        return result

    def verify_source(self, original, *, source_session_id: str) -> list[ForwardOutboxItem]:
        """Verify staged raw SHA + timing against immutable original records.

        Original may be a *closed* or quarantined crash-interrupted capture.
        Does not infer complete coverage of all original rows.
        """
        if source_session_id != self.session_id:
            raise ForwardOutboxError("original session differs from outbox")
        import hashlib
        import json
        items = self.items()
        if not items:
            raise ForwardOutboxError("no staged forward projections to recover")
        ids = set()
        for item in items:
            e = item.envelope
            if e.ingest_id in ids:
                raise ForwardOutboxError("duplicate staged source coordinate")
            ids.add(e.ingest_id)
            raw = original.verify_captured_record(source_session_id, e.ingest_id)
            data = json.loads(e.projection_json)
            if (
                raw.received_at_ms != e.available_at_ms
                or hashlib.sha256(raw.raw).hexdigest() != data["source_raw_sha256"]
                or raw.kind not in ("aggTrade", "snapshot", "depthUpdate")
                or ((data["topic"] == "md.aggTrade") != (raw.kind == "aggTrade"))
            ):
                raise ForwardOutboxError("staged envelope differs from captured original")
        return items
