"""Durable in-session checkpoints for independently ACKed forward projections.

A checkpoint becomes eligible ONLY after the original source, producer
outbox, HMAC receiver and protected read-only derived observers have
committed. An immutable hash chain binds every checkpoint's ordinal, source
capture ID, original projection fingerprint and receiver-owned receipt ID.

Checkpoint commits and source/receiver/observer commits are NOT one ACID
transaction; a process crash before checkpoint commit leaves a verifiable
missing *suffix*. Recovery must first validate ALL old checkpoints as an
exact prefix of fully reconciled outbox entries, and can then append the
missing suffix. Middle holes, rewritten entries or reordering fail closed.
No unknown exchange history, account state, order or broker is included.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope,
)
from infrastructure.spot_forward_outbox import ForwardOutboxItem

_GENESIS = "0"*64


class ForwardCheckpointError(ValueError):
    """Forward checkpoint source, chain or session consistency violation."""


@dataclass(frozen=True)
class ForwardCheckpointStatus:
    session_id: str
    checkpointed_events: int
    last_capture_id: int | None
    last_available_at_ms: int | None
    tail_sha256: str
    external_history_complete: bool = False
    market_feed_continuous: bool = False
    orders_submitted: int = 0


def _digest(previous: str, ordinal: int, e: ProjectionEnvelope,
            receipt_id: str) -> str:
    body=json.dumps({
        "previous":previous,
        "ordinal":ordinal,
        "session_event_id":e.event_id,
        "capture_id":e.ingest_id,
        "source_available_at_ms":e.available_at_ms,
        "projection_fingerprint":e.projection_fingerprint,
        "receiver_receipt_id":receipt_id,
    },sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class OfflineForwardSessionCheckpoints:
    """Single producer/owner SQLite WAL/FULL append-only receipt chain."""

    def __init__(
        self, path: Path, *, session_id: str,
        mode_provider: Callable[[],QuantMode],
        reuse_connection: bool = False,
    ):
        if (
            not isinstance(path,Path) or not path.is_absolute()
            or path.is_symlink() or path!=path.resolve()
            or not isinstance(session_id,str) or not session_id.startswith("rec_")
            or len(session_id)>48
            or not callable(mode_provider)
            or mode_provider() is not QuantMode.SIMULATION
        ):
            raise ForwardCheckpointError("invalid SIMULATION checkpoint ownership")
        if type(reuse_connection) is not bool:
            raise ForwardCheckpointError("invalid checkpoint connection reuse flag")
        self.path=path
        self._reuse_connection=reuse_connection
        self._session_connection: sqlite3.Connection | None = None
        self.session_id=session_id
        self._mode_provider=mode_provider
        path.parent.mkdir(parents=True,exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS forward_checkpoint_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    session_id TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS forward_checkpoint (
                    ordinal INTEGER PRIMARY KEY CHECK(ordinal>0),
                    capture_id INTEGER NOT NULL UNIQUE CHECK(capture_id>0),
                    source_event_id TEXT NOT NULL UNIQUE,
                    available_at_ms INTEGER NOT NULL,
                    projection_fingerprint TEXT NOT NULL,
                    receiver_receipt_id TEXT NOT NULL,
                    previous_sha256 TEXT NOT NULL,
                    checkpoint_sha256 TEXT NOT NULL
                )
            """)
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""
                    INSERT OR IGNORE INTO forward_checkpoint_identity
                    (singleton,session_id) VALUES (1,?)
                """,(session_id,))
                ident=db.execute("""
                    SELECT session_id FROM forward_checkpoint_identity WHERE singleton=1
                """).fetchone()
                if ident is None or ident[0]!=session_id:
                    raise ForwardCheckpointError("checkpoint source identity changed")
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        if self._reuse_connection:
            if self._session_connection is None:
                self._session_connection = sqlite3.connect(
                    self.path,isolation_level=None,timeout=10,
                )
                self._session_connection.row_factory=sqlite3.Row
                self._session_connection.execute("PRAGMA busy_timeout=10000")
            if self._session_connection.in_transaction:
                raise ForwardCheckpointError("unfinished checkpoint transaction")
            yield self._session_connection
        else:
            db=sqlite3.connect(self.path,isolation_level=None,timeout=10)
            try:
                db.row_factory=sqlite3.Row
                db.execute("PRAGMA busy_timeout=10000")
                yield db
            finally:
                db.close()

    def close(self) -> None:
        db=self._session_connection
        if db is None:
            return
        self._session_connection=None
        try:
            if db.in_transaction:
                db.rollback()
                raise ForwardCheckpointError("uncommitted checkpoint transaction on close")
        finally:
            db.close()

    def _sim(self):
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise ForwardCheckpointError("no checkpoint writes outside SIMULATION")

    def _expected(self, e: ProjectionEnvelope):
        if (
            type(e) is not ProjectionEnvelope
            or e.event_id!=f"md:forward:{self.session_id}:{e.ingest_id}"
            or e.source_consumer!="forward_spot"
            or e.receiver!="read_only_market"
        ):
            raise ForwardCheckpointError("untrusted source-bound checkpoint envelope")
        return OfflineIndependentProjectionReceiver._receipt_id(e)

    def verified_prefix(self, items: list[ForwardOutboxItem]) -> ForwardCheckpointStatus:
        """Read and independently validate chain against ordered source intents."""
        self._sim()
        with self._db() as db:
            rows=db.execute("SELECT * FROM forward_checkpoint ORDER BY ordinal").fetchall()
        if len(rows)>len(items):
            raise ForwardCheckpointError("checkpoint tail exceeds durable outbox")
        previous=_GENESIS
        for index,row in enumerate(rows):
            ordinal=index+1
            item=items[index]
            e=item.envelope
            receipt=self._expected(e)
            if (
                item.status!="DERIVED_ACKED"
                or row["ordinal"]!=ordinal
                or row["capture_id"]!=e.ingest_id
                or row["source_event_id"]!=e.event_id
                or row["available_at_ms"]!=e.available_at_ms
                or row["projection_fingerprint"]!=e.projection_fingerprint
                or row["receiver_receipt_id"]!=receipt
                or row["previous_sha256"]!=previous
                or row["checkpoint_sha256"]!=_digest(previous,ordinal,e,receipt)
            ):
                raise ForwardCheckpointError("checkpoint prefix hash/source mismatch")
            previous=row["checkpoint_sha256"]
        return ForwardCheckpointStatus(
            session_id=self.session_id,
            checkpointed_events=len(rows),
            last_capture_id=rows[-1]["capture_id"] if rows else None,
            last_available_at_ms=rows[-1]["available_at_ms"] if rows else None,
            tail_sha256=previous,
        )

    def append(self, e: ProjectionEnvelope, *,
               completed_outbox: list[ForwardOutboxItem]) -> ForwardCheckpointStatus:
        """Prove full ordered source prefix and append exactly its next event."""
        self._sim()
        receipt=self._expected(e)
        before=self.verified_prefix(completed_outbox)
        index=before.checkpointed_events
        if index>=len(completed_outbox):
            raise ForwardCheckpointError("no new source intent eligible for checkpoint")
        candidate=completed_outbox[index]
        if candidate.envelope!=e or candidate.status!="DERIVED_ACKED":
            raise ForwardCheckpointError("checkpoint may only follow completed ACK")
        if index and e.available_at_ms < before.last_available_at_ms:
            raise ForwardCheckpointError("checkpoint receipt chronology regressed")
        ordinal=index+1
        digest=_digest(before.tail_sha256,ordinal,e,receipt)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                last=db.execute("""
                    SELECT ordinal,checkpoint_sha256 FROM forward_checkpoint
                    ORDER BY ordinal DESC LIMIT 1
                """).fetchone()
                if (
                    (last is None and ordinal!=1)
                    or (last is not None and (
                        last["ordinal"]!=index
                        or last["checkpoint_sha256"]!=before.tail_sha256
                    ))
                ):
                    raise ForwardCheckpointError("concurrent checkpoint tail changed")
                db.execute("""
                    INSERT INTO forward_checkpoint
                    (ordinal,capture_id,source_event_id,available_at_ms,
                     projection_fingerprint,receiver_receipt_id,
                     previous_sha256,checkpoint_sha256)
                    VALUES (?,?,?,?,?,?,?,?)
                """,(ordinal,e.ingest_id,e.event_id,e.available_at_ms,
                     e.projection_fingerprint,receipt,before.tail_sha256,digest))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.verified_prefix(completed_outbox)

    def append_live(
        self, e: ProjectionEnvelope, *,
        completed_item: ForwardOutboxItem, expected_ordinal: int,
    ) -> ForwardCheckpointStatus:
        """O(1) latest-tail checkpoint after independently durable event ACK.

        The per-event hot path checks the exact indexed DERIVED_ACKED
        outbox row and source-scoped HMAC receipt, then commits one chained
        checkpoint under SQLite BEGIN IMMEDIATE. It does NOT reread the
        entire historical chain at every event. Existing chain integrity
        is FULLY reverified against immutable source/outbox/receiver/derived
        evidence at session close and by independent supervisor/recovery.
        A previous row tampered DURING an open session may therefore be
        caught at sealing, not immediately; it is never certified PASS.
        """
        self._sim()
        receipt=self._expected(e)
        if (
            type(expected_ordinal) is not int
            or not 1 <= expected_ordinal <= 5000
            or not isinstance(completed_item, ForwardOutboxItem)
            or completed_item.envelope != e
            or completed_item.status != "DERIVED_ACKED"
            or completed_item.receiver_receipt_id != receipt
        ):
            raise ForwardCheckpointError("live checkpoint missing exact complete ACK")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                identity=db.execute("""
                    SELECT session_id FROM forward_checkpoint_identity
                    WHERE singleton=1
                """).fetchone()
                if identity is None or identity["session_id"]!=self.session_id:
                    raise ForwardCheckpointError("checkpoint session identity changed")
                last=db.execute("""
                    SELECT ordinal,checkpoint_sha256,available_at_ms,capture_id
                    FROM forward_checkpoint
                    ORDER BY ordinal DESC LIMIT 1
                """).fetchone()
                ordinal=(last["ordinal"]+1 if last is not None else 1)
                previous=(
                    last["checkpoint_sha256"] if last is not None else _GENESIS
                )
                if (
                    ordinal != expected_ordinal
                    or (last is not None and (
                        e.ingest_id <= last["capture_id"]
                        or e.available_at_ms < last["available_at_ms"]
                    ))
                    or len(previous)!=64
                    or any(c not in "0123456789abcdef" for c in previous)
                ):
                    raise ForwardCheckpointError("live checkpoint tail/outbox order mismatch")
                digest=_digest(previous,ordinal,e,receipt)
                db.execute("""
                    INSERT INTO forward_checkpoint (
                        ordinal,capture_id,source_event_id,available_at_ms,
                        projection_fingerprint,receiver_receipt_id,
                        previous_sha256,checkpoint_sha256
                    ) VALUES (?,?,?,?,?,?,?,?)
                """,(ordinal,e.ingest_id,e.event_id,e.available_at_ms,
                     e.projection_fingerprint,receipt,previous,digest))
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return ForwardCheckpointStatus(
            session_id=self.session_id,
            checkpointed_events=ordinal,
            last_capture_id=e.ingest_id,
            last_available_at_ms=e.available_at_ms,
            tail_sha256=digest,
        )

    def reconcile(self, completed_outbox: list[ForwardOutboxItem]) -> ForwardCheckpointStatus:
        """After independent source/derived replay, repair only missing suffix."""
        self.verified_prefix(completed_outbox)
        state=self.verified_prefix(completed_outbox)
        for item in completed_outbox[state.checkpointed_events:]:
            if item.status!="DERIVED_ACKED":
                raise ForwardCheckpointError("cannot certify unacknowledged outbox")
            state=self.append(item.envelope,completed_outbox=completed_outbox)
        return state
