"""Phase 0.5: offline-only durable admission and read-only reconciliation model.

This module NEVER imports CCXT or places broker orders. It is a reference
implementation for a future, independently reviewed execution admission gate.
A repeated event with the same intent_id/payload is admitted once; conflicting
reuses of that ID fail closed. An interrupted submission is UNKNOWN and must
be reconciled using the ORIGINAL stable client order ID, not blindly retried.
"""
from __future__ import annotations

import hashlib
import math
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Mapping

ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
STATUS = ("ADMITTED", "SUBMITTING", "UNKNOWN", "ACKNOWLEDGED", "RECONCILED", "REJECTED")
BrokerStatus = Literal["OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "NOT_FOUND"]


class IntentConflict(ValueError):
    """An existing intent_id was replayed with different order semantics."""


class ReconciliationError(RuntimeError):
    """Missing or contradictory broker evidence; no automatic retry permitted."""


@dataclass(frozen=True)
class Admission:
    intent_id: str
    client_order_id: str
    payload_fingerprint: str
    status: str
    created: bool = False
    broker_order_id: str | None = None
    broker_status: str | None = None
    filled_quantity: float = 0.0


@dataclass(frozen=True)
class BrokerObservation:
    client_order_id: str
    status: BrokerStatus
    broker_order_id: str | None = None
    filled_quantity: float = 0.0


def stable_client_order_id(intent_id: str) -> str:
    """A stable <= 36-char venue-like identifier; not submission permission."""
    if not isinstance(intent_id, str) or not ID_PATTERN.fullmatch(intent_id):
        raise ValueError("invalid event-origin intent_id")
    return "ECN-" + hashlib.sha256(intent_id.encode("utf-8")).hexdigest()[:28]


def fingerprint(payload: Mapping[str, object]) -> str:
    """Hash the complete immutable business intent, not timestamps or receipts."""
    try:
        serialized = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("intent payload must be finite JSON data") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class OfflineAdmissionLedger:
    """Durable SQLite-only admission; *no* broker submission/connection methods.

    One new short-lived SQLite connection per transaction permits multiple
    instances/processes to serialize admissions via BEGIN IMMEDIATE + UNIQUE.
    State decisions are explicit. Opening a ledger does not replay any order.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS offline_intent_admission (
                    intent_id TEXT PRIMARY KEY,
                    client_order_id TEXT NOT NULL UNIQUE,
                    payload_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('ADMITTED','SUBMITTING','UNKNOWN',
                                   'ACKNOWLEDGED','RECONCILED','REJECTED')
                    ),
                    broker_order_id TEXT,
                    broker_status TEXT,
                    filled_quantity REAL NOT NULL DEFAULT 0 CHECK (filled_quantity >= 0),
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
            """)
            # One broker acknowledgement must never be credited to two intents.
            # SQLite UNIQUE permits many NULL (not yet acknowledged) values.
            connection.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                    offline_intent_broker_order_unique
                ON offline_intent_admission (broker_order_id)
                WHERE broker_order_id IS NOT NULL
            """)

    @contextmanager
    def _connect(self):
        """Always close the descriptor, including if a transaction fails."""
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _record(row: sqlite3.Row, *, created: bool = False) -> Admission:
        return Admission(
            intent_id=row["intent_id"], client_order_id=row["client_order_id"],
            payload_fingerprint=row["payload_fingerprint"], status=row["status"],
            created=created, broker_order_id=row["broker_order_id"],
            broker_status=row["broker_status"],
            filled_quantity=float(row["filled_quantity"]),
        )

    def get(self, intent_id: str) -> Admission | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM offline_intent_admission WHERE intent_id=?",
                (intent_id,),
            ).fetchone()
        return self._record(row) if row is not None else None

    def admit(self, intent_id: str, payload: Mapping[str, object]) -> Admission:
        """Atomic first admission. Duplicate returns created=False, never re-dispatch."""
        cid = stable_client_order_id(intent_id)
        fp = fingerprint(payload)
        now = time.time_ns() // 1_000_000
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT * FROM offline_intent_admission WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                if existing is not None:
                    if existing["payload_fingerprint"] != fp:
                        raise IntentConflict("intent_id reused with conflicting payload")
                    conn.execute("COMMIT")
                    return self._record(existing)
                conn.execute(
                    """INSERT INTO offline_intent_admission
                       (intent_id,client_order_id,payload_fingerprint,status,
                        created_at_ms,updated_at_ms)
                       VALUES (?,?,?,'ADMITTED',?,?)""",
                    (intent_id, cid, fp, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM offline_intent_admission WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                conn.execute("COMMIT")
                return self._record(row, created=True)
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def begin_submission(self, intent_id: str) -> bool:
        """Only records an attempted dispatch. NEVER sends to any broker.

        Returns True exactly once. Callers must separately guard venue access.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    """UPDATE offline_intent_admission
                       SET status='SUBMITTING', updated_at_ms=?
                       WHERE intent_id=? AND status='ADMITTED'""",
                    (time.time_ns() // 1_000_000, intent_id),
                )
                conn.execute("COMMIT")
                return cur.rowcount == 1
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def quarantine_interrupted(self) -> int:
        """Crash recovery: never promote in-flight submissions to retryable."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cur = conn.execute(
                    """UPDATE offline_intent_admission
                       SET status='UNKNOWN', updated_at_ms=?
                       WHERE status='SUBMITTING'""",
                    (time.time_ns() // 1_000_000,),
                )
                conn.execute("COMMIT")
                return cur.rowcount
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def unresolved(self) -> list[Admission]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM offline_intent_admission
                   WHERE status IN ('SUBMITTING','UNKNOWN','ACKNOWLEDGED')
                   ORDER BY created_at_ms,intent_id"""
            ).fetchall()
        return [self._record(row) for row in rows]

    def reconcile_read_only(
        self, intent_id: str, lookup: Callable[[str], BrokerObservation],
    ) -> Admission:
        """Consult a *supplied* read-only broker view, NEVER resubmit an order.

        NOT_FOUND and lookup failures remain UNKNOWN: eventual consistency or
        lost acknowledgements make auto-retry unsafe. A terminal state requires
        a matching client ID AND nonempty broker order ID.
        """
        entry = self.get(intent_id)
        if entry is None:
            raise KeyError(intent_id)
        if entry.status in ("RECONCILED", "REJECTED"):
            return entry
        if entry.status == "ADMITTED":
            raise ReconciliationError("unsubmitted intent cannot be reconciled")
        try:
            observation = lookup(entry.client_order_id)
        except Exception as exc:
            self._hold_unknown(intent_id)
            raise ReconciliationError("broker lookup unavailable; intent held UNKNOWN") from exc

        if not isinstance(observation, BrokerObservation):
            self._hold_unknown(intent_id)
            raise ReconciliationError("invalid broker observation; intent held UNKNOWN")
        if observation.client_order_id != entry.client_order_id:
            self._hold_unknown(intent_id)
            raise ReconciliationError("broker client order ID mismatch; intent held UNKNOWN")
        if observation.status not in (
            "OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "NOT_FOUND"
        ):
            self._hold_unknown(intent_id)
            raise ReconciliationError("unknown broker order status")
        if observation.status == "NOT_FOUND" or not observation.broker_order_id:
            self._hold_unknown(intent_id)
            return self.get(intent_id)  # not-found is never proof of no submission
        if not math.isfinite(observation.filled_quantity) or observation.filled_quantity < 0:
            self._hold_unknown(intent_id)
            raise ReconciliationError("invalid broker fill quantity")
        if observation.status in ("PARTIALLY_FILLED", "FILLED") and observation.filled_quantity <= 0:
            self._hold_unknown(intent_id)
            raise ReconciliationError("broker claims a fill without positive filled quantity")
        if observation.filled_quantity < entry.filled_quantity:
            self._hold_unknown(intent_id)
            raise ReconciliationError("broker filled quantity regressed; require human reconciliation")

        terminal = observation.status in ("FILLED", "CANCELED", "REJECTED")
        new_status = (
            "REJECTED" if observation.status == "REJECTED"
            else "RECONCILED" if terminal else "ACKNOWLEDGED"
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT * FROM offline_intent_admission WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                if current["status"] in ("RECONCILED", "REJECTED"):
                    conn.execute("COMMIT")
                    return self._record(current)
                if current["broker_order_id"] and current["broker_order_id"] != observation.broker_order_id:
                    # A contradictory broker reference is quarantined rather
                    # than silently replacing previously verified evidence.
                    conn.execute(
                        "UPDATE offline_intent_admission SET status='UNKNOWN' WHERE intent_id=?",
                        (intent_id,),
                    )
                    conn.execute("COMMIT")
                    raise ReconciliationError("conflicting broker order ID; intent held UNKNOWN")
                try:
                    conn.execute(
                        """UPDATE offline_intent_admission
                           SET status=?,broker_order_id=?,broker_status=?,
                               filled_quantity=?,updated_at_ms=? WHERE intent_id=?""",
                        (new_status, observation.broker_order_id, observation.status,
                         observation.filled_quantity, time.time_ns() // 1_000_000, intent_id),
                    )
                except sqlite3.IntegrityError as exc:
                    # Collision with a different admitted intent. Quarantine
                    # this admission; do not claim the same broker fill twice.
                    conn.execute(
                        """UPDATE offline_intent_admission
                           SET status='UNKNOWN',updated_at_ms=? WHERE intent_id=?""",
                        (time.time_ns() // 1_000_000, intent_id),
                    )
                    conn.execute("COMMIT")
                    raise ReconciliationError(
                        "broker order already reconciled to a different intent"
                    ) from exc
                row = conn.execute(
                    "SELECT * FROM offline_intent_admission WHERE intent_id=?",
                    (intent_id,),
                ).fetchone()
                conn.execute("COMMIT")
                return self._record(row)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _hold_unknown(self, intent_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE offline_intent_admission
                   SET status='UNKNOWN',updated_at_ms=?
                   WHERE intent_id=? AND status IN ('SUBMITTING','UNKNOWN','ACKNOWLEDGED')""",
                (time.time_ns() // 1_000_000, intent_id),
            )
