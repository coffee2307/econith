"""Optional durable SIMULATION-only fencing lease for local Spot capture workers.

Lease acquisition, renewal, generation fencing and release use BEGIN IMMEDIATE
transactions in the SAME SQLite file as capture attempts. The monotonic epoch
prevents stale workers committing after expiry/re-acquisition even when they
still hold old credentials in memory. This does not kill an external process,
fence the network socket, or authorize a broker side effect.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable

from infrastructure.spot_ingress_capture import (
    OfflineSpotCaptureJournal, SpotCaptureError,
)


class WorkerLeaseConflict(SpotCaptureError):
    """Another worker owns the lease or the worker's token is stale."""


@dataclass(frozen=True)
class WorkerLease:
    controller_id: str
    holder_id: str
    generation: int
    token: str
    expires_at_ms: int


@dataclass(frozen=True)
class WorkerLeaseStatus:
    controller_id: str
    holder_id: str | None
    generation: int
    expires_at_ms: int
    active: bool


class OfflineSpotWorkerFence:
    """Single SQLite controller lease; all timestamps are caller clock values.

    A validated local clock is required for real integrations. This component
    cannot prove NTP correctness or a peer's actual time. After loss of a
    lease, former workers MUST stop and must never commit a controller ACK.
    """

    def __init__(
        self, journal: OfflineSpotCaptureJournal, *,
        controller_id: str, clock_ms: Callable[[], int] | None = None,
        lease_ms: int = 30_000,
    ) -> None:
        if not isinstance(journal, OfflineSpotCaptureJournal):
            raise ValueError("offline capture journal required")
        journal._simulation_only()
        self.controller_id = journal._part(controller_id, "controller_id")
        if type(lease_ms) is not int or not 1_000 <= lease_ms <= 300_000:
            raise ValueError("lease_ms must be between 1000 and 300000")
        if clock_ms is not None and not callable(clock_ms):
            raise ValueError("clock_ms must be a callable")
        self.journal = journal
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.lease_ms = lease_ms
        with journal._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_worker_leases (
                    controller_id TEXT PRIMARY KEY,
                    holder_id TEXT NOT NULL,
                    generation INTEGER NOT NULL CHECK (generation > 0),
                    token TEXT NOT NULL,
                    acquired_at_ms INTEGER NOT NULL,
                    renewed_at_ms INTEGER NOT NULL,
                    expires_at_ms INTEGER NOT NULL
                )
            """)

    def _now(self) -> int:
        self.journal._simulation_only()
        return self.journal._positive(self.clock_ms(), "worker lease clock")

    @staticmethod
    def require_current(
        conn: sqlite3.Connection, claim: WorkerLease | None,
        *, controller_id: str, now_ms: int,
    ) -> None:
        if not isinstance(claim, WorkerLease) or claim.controller_id != controller_id:
            raise WorkerLeaseConflict("valid owner lease is required")
        row = conn.execute("""
            SELECT * FROM offline_spot_worker_leases WHERE controller_id=?
        """, (controller_id,)).fetchone()
        if (
            row is None or row["token"] != claim.token
            or row["holder_id"] != claim.holder_id
            or row["generation"] != claim.generation
            or row["expires_at_ms"] != claim.expires_at_ms
            or now_ms >= row["expires_at_ms"]
            or now_ms < row["renewed_at_ms"]
        ):
            raise WorkerLeaseConflict("worker lease expired, lost or superseded")

    def acquire(self, *, holder_id: str) -> WorkerLease:
        self.journal._simulation_only()
        holder = self.journal._part(holder_id, "holder_id")
        now = self._now()
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                old = conn.execute("""
                    SELECT * FROM offline_spot_worker_leases WHERE controller_id=?
                """, (self.controller_id,)).fetchone()
                if old is not None:
                    if now < old["renewed_at_ms"]:
                        raise WorkerLeaseConflict("clock regressed before lease acquisition")
                    if old["expires_at_ms"] > now:
                        raise WorkerLeaseConflict("worker lease already held")
                generation = 1 if old is None else old["generation"] + 1
                token = secrets.token_hex(24)
                expires = now + self.lease_ms
                conn.execute("""
                    INSERT INTO offline_spot_worker_leases
                    (controller_id,holder_id,generation,token,
                     acquired_at_ms,renewed_at_ms,expires_at_ms)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(controller_id) DO UPDATE SET
                        holder_id=excluded.holder_id,
                        generation=excluded.generation, token=excluded.token,
                        acquired_at_ms=excluded.acquired_at_ms,
                        renewed_at_ms=excluded.renewed_at_ms,
                        expires_at_ms=excluded.expires_at_ms
                """, (self.controller_id, holder, generation, token,
                      now, now, expires))
                conn.execute("COMMIT")
                return WorkerLease(self.controller_id, holder, generation, token, expires)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def renew(self, claim: WorkerLease) -> WorkerLease:
        self.journal._simulation_only()
        now = self._now()
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self.require_current(
                    conn, claim, controller_id=self.controller_id, now_ms=now,
                )
                expires = now + self.lease_ms
                conn.execute("""
                    UPDATE offline_spot_worker_leases
                    SET renewed_at_ms=?, expires_at_ms=?
                    WHERE controller_id=? AND generation=? AND token=?
                """, (now, expires, self.controller_id, claim.generation, claim.token))
                conn.execute("COMMIT")
                return WorkerLease(self.controller_id, claim.holder_id,
                                   claim.generation, claim.token, expires)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def release(self, claim: WorkerLease) -> None:
        """Only the active generation can release; keep row for epoch history."""
        self.journal._simulation_only()
        now = self._now()
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self.require_current(
                    conn, claim, controller_id=self.controller_id, now_ms=now,
                )
                conn.execute("""
                    UPDATE offline_spot_worker_leases
                    SET expires_at_ms=? WHERE controller_id=?
                """, (now, self.controller_id))
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def status(self, *, now_ms: int | None = None) -> WorkerLeaseStatus:
        self.journal._simulation_only()
        now = self._now() if now_ms is None else self.journal._positive(
            now_ms, "status clock"
        )
        with self.journal._connect() as conn:
            row = conn.execute("""
                SELECT holder_id,generation,expires_at_ms,renewed_at_ms
                FROM offline_spot_worker_leases WHERE controller_id=?
            """, (self.controller_id,)).fetchone()
        if row is None:
            return WorkerLeaseStatus(self.controller_id, None, 0, 0, False)
        if now < row["renewed_at_ms"]:
            raise WorkerLeaseConflict("status clock regressed")
        active = row["expires_at_ms"] > now
        return WorkerLeaseStatus(
            self.controller_id, row["holder_id"] if active else None,
            row["generation"], row["expires_at_ms"], active,
        )
