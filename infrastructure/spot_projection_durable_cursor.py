"""Durable source-cursor for opt-in asynchronous Phase 1 research projection.

Each cursor COMMIT acknowledges a *processed* immutable original, including
valid pre-book originals without a projection. Source and consumer ledgers
remain separate: no cross-DB atomicity is claimed. After an unclean crash,
closed-session recovery must independently reconcile the original, outbox,
receiver, derived receipts and checkpoint before any new research use.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import CapturedSpotRecord, OfflineSpotCaptureJournal


class ProjectionCursorError(ValueError):
    """Spool cursor/source diverged or its monotonic order was violated."""


@dataclass(frozen=True)
class ProjectionCursorState:
    session_id: str
    last_capture_id: int
    last_original_sha256: str
    last_available_at_ms: int
    source_continuity_certified: bool = False
    live_trading_authorized: bool = False


class OfflineSpotProjectionCursor:
    """SIMULATION-only, single-owner, FULL synchronous monotonic cursor."""

    def __init__(self, path: Path, *, journal: OfflineSpotCaptureJournal,
                 session_id: str):
        if (not isinstance(path, Path) or not path.is_absolute()
            or path != path.resolve() or path.is_symlink()
            or not isinstance(journal, OfflineSpotCaptureJournal)
            or not journal.forward_capture_audit
            or path == journal.path or path.parent != journal.path.parent
            or not isinstance(session_id, str) or not session_id.startswith("rec_")
            or len(session_id) > 48):
            raise ProjectionCursorError("unsafe independent spool cursor identity")
        journal._simulation_only()
        self.path = path
        self.journal = journal
        self.session_id = session_id
        with self._open() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS spot_projection_cursor (
                session_id TEXT PRIMARY KEY,
                last_capture_id INTEGER NOT NULL CHECK(last_capture_id>=0),
                original_sha256 TEXT NOT NULL,
                last_available_at_ms INTEGER NOT NULL CHECK(last_available_at_ms>=0)
            )""")
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""INSERT OR IGNORE INTO spot_projection_cursor
                    (session_id,last_capture_id,original_sha256,last_available_at_ms)
                    VALUES (?,0,?,0)""",(session_id,"0"*64))
                rows=db.execute("SELECT session_id FROM spot_projection_cursor").fetchall()
                if len(rows)!=1 or rows[0][0]!=session_id:
                    raise ProjectionCursorError("cursor identity diverged")
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def _open(self):
        db=sqlite3.connect(self.path,isolation_level=None,timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _state(self, db:sqlite3.Connection)->ProjectionCursorState:
        row=db.execute("""SELECT session_id,last_capture_id,original_sha256,
            last_available_at_ms FROM spot_projection_cursor WHERE session_id=?
        """,(self.session_id,)).fetchone()
        if row is None or len(row)!=4:
            raise ProjectionCursorError("missing immutable cursor identity")
        return ProjectionCursorState(*row)

    def read_verified(self)->ProjectionCursorState:
        self.journal._simulation_only()
        with self._open() as db:
            state=self._state(db)
        if state.last_capture_id:
            source=self.journal.verify_captured_record(
                self.session_id,state.last_capture_id,
            )
            digest=hashlib.sha256(source.raw).hexdigest()
            if (digest!=state.last_original_sha256
                or source.received_at_ms!=state.last_available_at_ms):
                raise ProjectionCursorError("cursor moved away from immutable original")
        elif (state.last_original_sha256!="0"*64
              or state.last_available_at_ms!=0):
            raise ProjectionCursorError("cursor genesis tampered")
        return state

    def advance(self, record:CapturedSpotRecord)->ProjectionCursorState:
        self.journal._simulation_only()
        if (not isinstance(record,CapturedSpotRecord)
            or record.session_id!=self.session_id):
            raise ProjectionCursorError("foreign source cursor advancement")
        # Reread exact immutable original AND atomic capture intent before COMMIT.
        exact=self.journal.verify_captured_record(
            self.session_id,record.capture_id,
        )
        if exact!=record:
            raise ProjectionCursorError("cursor advanced with forged original")
        digest=hashlib.sha256(record.raw).hexdigest()
        with self._open() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                prior=self._state(db)
                if (record.capture_id!=prior.last_capture_id+1
                    or record.received_at_ms<prior.last_available_at_ms):
                    raise ProjectionCursorError("cursor must advance one original in order")
                db.execute("""UPDATE spot_projection_cursor
                    SET last_capture_id=?,original_sha256=?,last_available_at_ms=?
                    WHERE session_id=? AND last_capture_id=?
                """,(record.capture_id,digest,record.received_at_ms,
                     self.session_id,prior.last_capture_id))
                if db.execute("SELECT changes()").fetchone()[0]!=1:
                    raise ProjectionCursorError("concurrent cursor generation change")
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self.read_verified()
