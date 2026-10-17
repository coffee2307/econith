"""Durable, explicitly enabled SIMULATION-only AI signal outbox.

This module has no broker integration and is NOT activated by default.
It records a predictor decision before publishing ai.signal onto EventBus.
Recovery reuses the persisted ID and exact serialized payload, never
reconstructs a signal from wall clock or the latest market state.

Queue acceptance is not consumer acknowledgement. QUEUED rows stay
replayable until downstream explicitly acknowledges; a repeated delivery
must be deduplicated at the receiving intent ledger.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Iterator

from core.mode import QuantMode
from quant.offline_admission import fingerprint, stable_client_order_id

_NAMESPACE = re.compile(r"^[A-Za-z0-9_-]{1,24}$")


@dataclass(frozen=True)
class StoredSignal:
    signal_id: str
    payload: dict[str, Any]
    status: str


def derive_leg_intent_id(signal_id: str, leg_index: int, symbol: str) -> str:
    """Stable per-leg ID from a previously recorded predictor signal ID."""
    stable_client_order_id(signal_id)  # validates origin grammar
    if not isinstance(leg_index, int) or isinstance(leg_index, bool) or leg_index < 0:
        raise ValueError("leg index must be a nonnegative integer")
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z0-9]{3,20}", symbol):
        raise ValueError("invalid routed symbol")
    seed = f"{signal_id}|{leg_index}|{symbol}".encode("utf-8")
    return "intent:" + hashlib.sha256(seed).hexdigest()[:40]


class OfflineSignalOutbox:
    """Persistent signal envelope; no automatic dispatch or capital authority."""

    def __init__(self, path: Path | str, *, namespace: str = "research") -> None:
        if not _NAMESPACE.fullmatch(namespace):
            raise ValueError("namespace must contain 1-24 alphanumeric/_/- chars")
        self.path = Path(path)
        self.namespace = namespace
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_signal_outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    namespace TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('STAGED','QUEUED','ACKED')),
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_frozen_route_plans (
                    signal_id TEXT PRIMARY KEY,
                    signal_fingerprint TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_fingerprint TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _encode(payload: Mapping[str, Any]) -> str:
        if not isinstance(payload, Mapping) or "signal_id" in payload:
            raise ValueError("signal_id belongs to the outbox and cannot be supplied")
        try:
            return json.dumps(
                dict(payload), sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("signal payload must contain finite JSON values") from exc

    @staticmethod
    def _decode(row: sqlite3.Row) -> StoredSignal:
        return StoredSignal(
            signal_id=f"sig:{row['namespace']}:{row['sequence']}",
            payload={
                **json.loads(row["payload_json"]),
                "signal_id": f"sig:{row['namespace']}:{row['sequence']}",
            },
            status=row["status"],
        )

    def stage(self, payload: Mapping[str, Any]) -> StoredSignal:
        """Atomic durable write that must finish before any bus publish."""
        serialized = self._encode(payload)
        now = time.time_ns() // 1_000_000
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    """INSERT INTO offline_signal_outbox
                       (namespace,payload_json,status,created_at_ms,updated_at_ms)
                       VALUES (?,?,'STAGED',?,?)""",
                    (self.namespace, serialized, now, now),
                )
                row = conn.execute(
                    "SELECT * FROM offline_signal_outbox WHERE sequence=?",
                    (cursor.lastrowid,),
                ).fetchone()
                conn.execute("COMMIT")
                return self._decode(row)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise


    def freeze_route_plan(
        self,
        signal_id: str,
        route_payload: Mapping[str, Any],
        intents: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Persist the COMPLETE immutable simulation plan before any leg is sent.

        A replay must read this snapshot, never re-size from live marks or
        in-memory exposure. Concurrent first writers serialize in SQLite;
        changed business data cannot replace an existing plan.
        """
        if not isinstance(intents, list) or not 1 <= len(intents) <= 32:
            raise ValueError("frozen plan requires 1-32 routed legs")
        if not isinstance(route_payload, Mapping):
            raise ValueError("route metadata must be a mapping")
        self._parts(signal_id)
        for index, leg in enumerate(intents):
            if not isinstance(leg, Mapping):
                raise ValueError("invalid routed leg")
            if (
                leg.get("signal_id") != signal_id
                or leg.get("leg_index") != index
                or leg.get("leg_count") != len(intents)
                or leg.get("intent_id") != derive_leg_intent_id(
                    signal_id, index, leg.get("symbol")
                )
                or leg.get("side") not in ("BUY", "SELL")
                or not isinstance(leg.get("reduce_only"), bool)
            ):
                raise ValueError("frozen leg identity or semantics mismatch")
        snapshot = {"route": dict(route_payload), "intents": [dict(x) for x in intents]}
        serialized = self._encode(snapshot)
        stored_snapshot = json.loads(serialized)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                namespace, sequence = self._parts(signal_id)
                origin = conn.execute(
                    "SELECT * FROM offline_signal_outbox WHERE namespace=? AND sequence=?",
                    (namespace, sequence),
                ).fetchone()
                if origin is None or origin["status"] == "ACKED":
                    raise ValueError("missing or already acknowledged outbox source")
                source_fp = fingerprint(self._decode(origin).payload)
                existing = conn.execute(
                    "SELECT * FROM offline_frozen_route_plans WHERE signal_id=?",
                    (signal_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["signal_fingerprint"] != source_fp
                        or existing["snapshot_json"] != serialized
                        or existing["snapshot_fingerprint"] != fingerprint(stored_snapshot)
                    ):
                        raise ValueError("immutable frozen route plan conflict")
                else:
                    conn.execute(
                        """INSERT INTO offline_frozen_route_plans
                           (signal_id,signal_fingerprint,snapshot_json,
                            snapshot_fingerprint,created_at_ms)
                           VALUES (?,?,?,?,?)""",
                        (
                            signal_id, source_fp, serialized,
                            fingerprint(stored_snapshot), time.time_ns() // 1_000_000,
                        ),
                    )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return stored_snapshot

    def frozen_route_plan(self, signal_id: str) -> dict[str, Any] | None:
        """Load a verified stored plan without recomputing market-dependent legs."""
        origin = self.get(signal_id)
        if origin is None:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM offline_frozen_route_plans WHERE signal_id=?",
                (signal_id,),
            ).fetchone()
        if row is None:
            return None
        snapshot = json.loads(row["snapshot_json"])
        if (
            row["signal_fingerprint"] != fingerprint(origin.payload)
            or row["snapshot_fingerprint"] != fingerprint(snapshot)
            or self._encode(snapshot) != row["snapshot_json"]
        ):
            raise ValueError("frozen route plan integrity mismatch")
        return snapshot

    def mark_queued(self, signal_id: str) -> None:
        """Queued does NOT mean consumed; this cannot retire a replay."""
        row = self.get(signal_id)
        if row is None:
            raise KeyError(signal_id)
        with self._connect() as conn:
            conn.execute(
                """UPDATE offline_signal_outbox
                   SET status='QUEUED',updated_at_ms=?
                   WHERE namespace=? AND sequence=? AND status='STAGED'""",
                (time.time_ns() // 1_000_000, *self._parts(signal_id)),
            )

    def acknowledge(self, signal_id: str) -> None:
        """Only an explicit downstream-confirmed receipt can retire replay."""
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE offline_signal_outbox
                   SET status='ACKED', updated_at_ms=?
                   WHERE namespace=? AND sequence=? AND status='QUEUED'""",
                (time.time_ns() // 1_000_000, *self._parts(signal_id)),
            )
        if cur.rowcount != 1:
            raise ValueError("only a queued, non-acknowledged signal can be acknowledged")

    def _parts(self, signal_id: str) -> tuple[str, int]:
        prefix = f"sig:{self.namespace}:"
        if not isinstance(signal_id, str) or not signal_id.startswith(prefix):
            raise ValueError("signal ID does not belong to this namespace")
        index = signal_id[len(prefix):]
        if not index.isdecimal() or int(index) <= 0 or str(int(index)) != index:
            raise ValueError("invalid outbox sequence")
        return self.namespace, int(index)

    def get(self, signal_id: str) -> StoredSignal | None:
        namespace, sequence = self._parts(signal_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM offline_signal_outbox WHERE namespace=? AND sequence=?",
                (namespace, sequence),
            ).fetchone()
        return self._decode(row) if row is not None else None

    def pending(self) -> list[StoredSignal]:
        """Manual replay list, never automatically routed to any executor."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM offline_signal_outbox
                   WHERE namespace=? AND status IN ('STAGED','QUEUED')
                   ORDER BY sequence""",
                (self.namespace,),
            ).fetchall()
        return [self._decode(row) for row in rows]

    async def publish_to_simulation(self, bus, signal: StoredSignal, *, mode: QuantMode) -> None:
        """Publish a previously persisted envelope only inside SIMULATION."""
        if mode is not QuantMode.SIMULATION:
            raise RuntimeError("offline signal outbox cannot publish outside SIMULATION")
        persisted = self.get(signal.signal_id)
        if persisted is None or persisted.payload != signal.payload or persisted.status == "ACKED":
            raise ValueError("uncommitted, altered or already-acknowledged envelope")
        await bus.publish("ai.signal", **persisted.payload)
        self.mark_queued(signal.signal_id)
