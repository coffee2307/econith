"""SIMULATION-only durable Binance Spot capture retry and outage ledger.

No implicit sockets, no trading, no EventBus publishing, no unbounded retry.
A *successful later snapshot* only restores present-time book availability.
It NEVER proves that unseen events in the outage interval were recovered.
"""
from __future__ import annotations

import asyncio
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from infrastructure.spot_ingress_capture import (
    OfflineSpotCaptureJournal, SpotCaptureError,
)
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportResult,
)
from infrastructure.spot_worker_fence import (
    OfflineSpotWorkerFence, WorkerLease, WorkerLeaseConflict,
)


class ReconnectError(SpotCaptureError):
    """Fail-closed, durable reconnect protocol violation."""


@dataclass(frozen=True)
class CaptureAttempt:
    attempt_id: int
    controller_id: str
    sequence: int
    session_id: str
    started_at_ms: int
    finished_at_ms: int | None
    status: str
    failure_code: str | None
    frames: int
    last_update_id: int | None


@dataclass(frozen=True)
class CaptureOutage:
    outage_id: int
    controller_id: str
    started_at_ms: int
    restored_at_ms: int | None
    failed_attempts: int
    last_failure_code: str
    restored_by_session: str | None
    coverage_verified: bool


@dataclass(frozen=True)
class ReconnectRun:
    status: str
    sessions_attempted: int
    last_session_id: str | None
    latest_book_update_id: int | None
    open_outages: int
    history_continuity_verified: bool = False


class OfflineSpotReconnectController:
    """Record attempts *before* external capture and outcomes transactionally.

    An unclosed STARTED attempt on restart is permanently marked INTERRUPTED.
    Every retry uses a fresh ID and a fresh snapshot. A restored current book
    closes an outage, but its gap remains non-recoverable in the audit record.
    """

    def __init__(
        self, journal: OfflineSpotCaptureJournal, *,
        controller_id: str, symbol: str,
        clock_ms: Callable[[], int] | None = None,
        worker_fence: OfflineSpotWorkerFence | None = None,
    ) -> None:
        if not isinstance(journal, OfflineSpotCaptureJournal):
            raise ValueError("offline capture journal required")
        journal._simulation_only()
        self.controller_id = journal._part(controller_id, "controller_id")
        self.symbol = journal._symbol(symbol)
        self.journal = journal
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        if worker_fence is not None and (
            not isinstance(worker_fence, OfflineSpotWorkerFence)
            or worker_fence.journal.path.resolve() != journal.path.resolve()
            or worker_fence.controller_id != self.controller_id
        ):
            raise ValueError("worker fence must share controller identity and journal")
        self.worker_fence = worker_fence
        with journal._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_reconnect_controllers (
                    controller_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    requires_fence INTEGER NOT NULL DEFAULT 0
                        CHECK (requires_fence IN (0,1))
                )
            """)
            columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(offline_spot_reconnect_controllers)"
                )
            }
            if "requires_fence" not in columns:
                conn.execute("""
                    ALTER TABLE offline_spot_reconnect_controllers
                    ADD COLUMN requires_fence INTEGER NOT NULL DEFAULT 0
                """)
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO offline_spot_reconnect_controllers
                    (controller_id,symbol) VALUES (?,?)
                """, (self.controller_id, self.symbol))
                owned = conn.execute("""
                    SELECT symbol, requires_fence FROM offline_spot_reconnect_controllers
                    WHERE controller_id=?
                """, (self.controller_id,)).fetchone()
                if owned["symbol"] != self.symbol:
                    raise ReconnectError("controller ID already bound to another symbol")
                if self.worker_fence is not None:
                    conn.execute("""
                        UPDATE offline_spot_reconnect_controllers
                        SET requires_fence=1 WHERE controller_id=?
                    """, (self.controller_id,))
                elif owned["requires_fence"]:
                    raise ReconnectError("controller requires a durable worker fence")
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_reconnect_attempts (
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    controller_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    session_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    finished_at_ms INTEGER,
                    status TEXT NOT NULL CHECK (
                        status IN ('STARTED','SUCCESS','FAILED','INTERRUPTED')
                    ),
                    failure_code TEXT,
                    frames INTEGER NOT NULL DEFAULT 0,
                    last_update_id INTEGER,
                    UNIQUE(controller_id, sequence)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_reconnect_outages (
                    outage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    controller_id TEXT NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    restored_at_ms INTEGER,
                    failed_attempts INTEGER NOT NULL,
                    last_failure_code TEXT NOT NULL,
                    restored_by_session TEXT,
                    coverage_verified INTEGER NOT NULL DEFAULT 0 CHECK (
                        coverage_verified=0
                    )
                )
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS spot_one_open_outage
                ON offline_spot_reconnect_outages(controller_id)
                WHERE restored_at_ms IS NULL
            """)

    def _now(self) -> int:
        return self.journal._positive(self.clock_ms(), "controller clock")

    def _check_mutation_lease(
        self, conn: sqlite3.Connection, claim: WorkerLease | None,
        *, now_ms: int,
    ) -> None:
        policy = conn.execute("""
            SELECT requires_fence FROM offline_spot_reconnect_controllers
            WHERE controller_id=?
        """, (self.controller_id,)).fetchone()
        if policy is None:
            raise ReconnectError("controller policy missing")
        if policy["requires_fence"]:
            OfflineSpotWorkerFence.require_current(
                conn, claim, controller_id=self.controller_id, now_ms=now_ms,
            )
        elif claim is not None:
            raise WorkerLeaseConflict("fenced claim cannot enter legacy controller")

    @staticmethod
    def _attempt(row: sqlite3.Row) -> CaptureAttempt:
        return CaptureAttempt(
            row["attempt_id"], row["controller_id"], row["sequence"],
            row["session_id"], row["started_at_ms"], row["finished_at_ms"],
            row["status"], row["failure_code"], row["frames"],
            row["last_update_id"],
        )

    @staticmethod
    def _outage(row: sqlite3.Row) -> CaptureOutage:
        return CaptureOutage(
            row["outage_id"], row["controller_id"], row["started_at_ms"],
            row["restored_at_ms"], row["failed_attempts"],
            row["last_failure_code"], row["restored_by_session"], False,
        )

    def attempts(self) -> list[CaptureAttempt]:
        self.journal._simulation_only()
        with self.journal._connect() as conn:
            return [self._attempt(x) for x in conn.execute("""
                SELECT * FROM offline_spot_reconnect_attempts
                WHERE controller_id=? ORDER BY sequence
            """, (self.controller_id,))]

    def outages(self) -> list[CaptureOutage]:
        self.journal._simulation_only()
        with self.journal._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM offline_spot_reconnect_outages
                WHERE controller_id=? ORDER BY outage_id
            """, (self.controller_id,)).fetchall()
            return [self._outage(x) for x in rows]

    def _record_outage(
        self, conn: sqlite3.Connection, *,
        started_at_ms: int, failure_code: str,
    ) -> None:
        row = conn.execute("""
            SELECT outage_id FROM offline_spot_reconnect_outages
            WHERE controller_id=? AND restored_at_ms IS NULL
        """, (self.controller_id,)).fetchone()
        if row is None:
            conn.execute("""
                INSERT INTO offline_spot_reconnect_outages
                (controller_id,started_at_ms,failed_attempts,
                 last_failure_code,coverage_verified)
                VALUES (?,?,1,?,0)
            """, (self.controller_id, started_at_ms, failure_code))
        else:
            conn.execute("""
                UPDATE offline_spot_reconnect_outages
                SET failed_attempts=failed_attempts+1,
                    last_failure_code=?
                WHERE outage_id=?
            """, (failure_code, row["outage_id"]))

    def _complete(
        self, attempt_id: int, *,
        status: str, failure_code: str | None = None,
        result: SpotTransportResult | None = None,
        lease: WorkerLease | None = None,
    ) -> CaptureAttempt:
        self.journal._simulation_only()
        now = self._now()
        if status not in {"SUCCESS", "FAILED", "INTERRUPTED"}:
            raise ReconnectError("invalid final attempt state")
        if status == "SUCCESS":
            if (
                not isinstance(result, SpotTransportResult)
                or result.symbol != self.symbol
                or result.snapshot_captures != 1
                or result.depth_status != "SYNCED"
                or type(result.last_update_id) is not int
                or result.last_update_id <= 0
            ):
                raise ReconnectError("capture success without verified Spot book")
            # Callbacks can lie or return a stale result. Independently
            # reconstruct from journal bytes and require a closed session.
            replay = OfflineSpotCaptureReplayer(self.journal).replay(
                result.session_id, as_of_ms=result.last_received_at_ms,
            )
            if (
                replay.book is None
                or replay.book.last_update_id != result.last_update_id
                or sum(x.kind == "snapshot" for x in
                       self.journal.replay_as_of(result.session_id, result.last_received_at_ms)) != 1
            ):
                raise ReconnectError("unverified persisted depth before success")
            with self.journal._connect() as verify_conn:
                source = verify_conn.execute(
                    "SELECT * FROM offline_spot_capture_sessions WHERE session_id=?",
                    (result.session_id,),
                ).fetchone()
                if (
                    source is None
                    or source["closed_at_ms"] is None
                    or source["symbol"] != self.symbol
                    or source["last_receipt_ms"] > result.last_received_at_ms
                ):
                    raise ReconnectError("source session must be closed and match result")
        elif (
            not isinstance(failure_code, str)
            or not 1 <= len(failure_code) <= 80
            or not failure_code.replace("_", "").isalnum()
        ):
            raise ReconnectError("invalid bounded failure code")
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._check_mutation_lease(conn, lease, now_ms=now)
                row = conn.execute("""
                    SELECT * FROM offline_spot_reconnect_attempts
                    WHERE attempt_id=? AND controller_id=?
                """, (attempt_id, self.controller_id)).fetchone()
                if row is None or row["status"] != "STARTED":
                    raise ReconnectError("attempt missing or already finalized")
                if now < row["started_at_ms"]:
                    raise ReconnectError("non-monotonic attempt finalization")
                if row["symbol"] != self.symbol:
                    raise ReconnectError("attempt belongs to another instrument")
                if status == "SUCCESS" and result.session_id != row["session_id"]:
                    raise ReconnectError("capture result belongs to another session")
                if status == "SUCCESS":
                    source = conn.execute("""
                        SELECT * FROM offline_spot_capture_sessions
                        WHERE session_id=?
                    """, (row["session_id"],)).fetchone()
                    if (
                        source is None
                        or source["opened_at_ms"] < row["started_at_ms"]
                        or source["closed_at_ms"] > now
                        or result.last_received_at_ms > now
                    ):
                        raise ReconnectError("captured session clock outside attempt interval")
                conn.execute("""
                    UPDATE offline_spot_reconnect_attempts
                    SET finished_at_ms=?, status=?, failure_code=?,
                        frames=?, last_update_id=?
                    WHERE attempt_id=?
                """, (
                    now, status, failure_code,
                    result.websocket_frames if result is not None else 0,
                    result.last_update_id if result is not None else None,
                    attempt_id,
                ))
                if status == "SUCCESS":
                    conn.execute("""
                        UPDATE offline_spot_reconnect_outages
                        SET restored_at_ms=?, restored_by_session=?
                        WHERE controller_id=? AND restored_at_ms IS NULL
                    """, (now, row["session_id"], self.controller_id))
                else:
                    self._record_outage(
                        conn, started_at_ms=row["started_at_ms"],
                        failure_code=failure_code,
                    )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return self._attempt_by_id(attempt_id)

    def _attempt_by_id(self, attempt_id: int) -> CaptureAttempt:
        with self.journal._connect() as conn:
            row = conn.execute("""
                SELECT * FROM offline_spot_reconnect_attempts
                WHERE attempt_id=? AND controller_id=?
            """, (attempt_id, self.controller_id)).fetchone()
            if row is None:
                raise ReconnectError("attempt not found")
            return self._attempt(row)

    def reconcile_crashed_attempts(
        self, *, lease: WorkerLease | None = None,
    ) -> int:
        """Reconcile after confirmed worker death or fenced takeover."""
        self.journal._simulation_only()
        now = self._now()
        count = 0
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._check_mutation_lease(conn, lease, now_ms=now)
                rows = conn.execute("""
                    SELECT * FROM offline_spot_reconnect_attempts
                    WHERE controller_id=? AND status='STARTED'
                    ORDER BY sequence
                """, (self.controller_id,)).fetchall()
                for row in rows:
                    if row["symbol"] != self.symbol:
                        raise ReconnectError("incomplete attempt belongs to another instrument")
                    if now < row["started_at_ms"]:
                        raise ReconnectError("recovery clock precedes attempt start")
                    conn.execute("""
                        UPDATE offline_spot_reconnect_attempts
                        SET status='INTERRUPTED', finished_at_ms=?,
                            failure_code='PROCESS_INTERRUPTED'
                        WHERE attempt_id=? AND status='STARTED'
                    """, (now, row["attempt_id"]))
                    self._record_outage(
                        conn, started_at_ms=row["started_at_ms"],
                        failure_code="PROCESS_INTERRUPTED",
                    )
                    count += 1
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return count

    def begin_attempt(
        self, *, lease: WorkerLease | None = None,
    ) -> CaptureAttempt:
        self.journal._simulation_only()
        now = self._now()
        with self.journal._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._check_mutation_lease(conn, lease, now_ms=now)
                prev = conn.execute("""
                    SELECT * FROM offline_spot_reconnect_attempts
                    WHERE controller_id=? ORDER BY sequence DESC LIMIT 1
                """, (self.controller_id,)).fetchone()
                if prev is not None and prev["status"] == "STARTED":
                    raise ReconnectError("unreconciled in-flight attempt")
                if prev is not None and prev["symbol"] != self.symbol:
                    raise ReconnectError("controller history belongs to another symbol")
                if prev is not None and now < prev["finished_at_ms"]:
                    raise ReconnectError("reconnect clock regressed")
                sequence = 1 if prev is None else prev["sequence"] + 1
                session_id = f"rec_{secrets.token_hex(12)}"
                cursor = conn.execute("""
                    INSERT INTO offline_spot_reconnect_attempts
                    (controller_id,sequence,session_id,symbol,
                     started_at_ms,status) VALUES (?,?,?,?,?,'STARTED')
                """, (self.controller_id, sequence, session_id, self.symbol, now))
                conn.execute("COMMIT")
                attempt_id = cursor.lastrowid
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return self._attempt_by_id(attempt_id)

    async def run_batch(
        self,
        capture_once: Callable[[str, int], Awaitable[SpotTransportResult]],
        *,
        max_attempts: int = 3, backoff_s: float = 0.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        _lease_ref: Callable[[], WorkerLease] | None = None,
    ) -> ReconnectRun:
        """Bounded retries, distinct session+snapshot each time, durable causes.

        Caller-supplied capture callback must be a read-only opt-in adapter;
        the controller never grants an arbitrary callback trading privileges.
        """
        self.journal._simulation_only()
        if not callable(capture_once) or not callable(sleep):
            raise ValueError("async capture and sleep callables required")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 10:
            raise ValueError("retry budget must be 1..10")
        if type(backoff_s) not in (int, float) or not 0 <= backoff_s <= 30:
            raise ValueError("invalid bounded backoff")
        # Never automatically steal an active worker's STARTED attempt.
        # An operator must explicitly reconcile after confirmed process death.
        last_id: str | None = None
        book_id: int | None = None
        ran = 0
        for index in range(max_attempts):
            if index:
                await sleep(min(backoff_s * (2 ** (index - 1)), 30.0))
            self.journal._simulation_only()
            attempt = self.begin_attempt(
                lease=_lease_ref() if _lease_ref is not None else None,
            )
            ran += 1
            last_id = attempt.session_id
            try:
                result = await capture_once(attempt.session_id, attempt.sequence)
                self.journal._simulation_only()
                self._complete(
                    attempt.attempt_id, status="SUCCESS", result=result,
                    lease=_lease_ref() if _lease_ref is not None else None,
                )
                book_id = result.last_update_id
                break
            except asyncio.CancelledError:
                # Catch cancellation separately: do not pretend the session was
                # healthy. If bookkeeping is also interrupted, restart recovery
                # will durably mark STARTED as INTERRUPTED.
                self._complete(
                    attempt.attempt_id, status="INTERRUPTED",
                    failure_code="TASK_CANCELLED",
                    lease=_lease_ref() if _lease_ref is not None else None,
                )
                raise
            except Exception as exc:
                # No exception string: network exceptions may contain URL,
                # credentials, query arguments or other private context.
                code = (
                    exc.reason if isinstance(exc, SpotTransportError)
                    else "SOURCE_EVIDENCE_REJECTED"
                    if isinstance(exc, SpotCaptureError)
                    else type(exc).__name__
                )
                if not code.isidentifier() or len(code) > 80:
                    code = "CAPTURE_ERROR"
                self._complete(
                    attempt.attempt_id, status="FAILED",
                    failure_code=code,
                    lease=_lease_ref() if _lease_ref is not None else None,
                )
        return ReconnectRun(
            "RECOVERED" if book_id is not None else "EXHAUSTED",
            ran, last_id, book_id,
            sum(o.restored_at_ms is None for o in self.outages()), False,
        )

    async def run_fenced_batch(
        self,
        capture_once: Callable[[str, int], Awaitable[SpotTransportResult]],
        *,
        holder_id: str,
        max_attempts: int = 3,
        backoff_s: float = 0.0,
        heartbeat_interval_s: float = 5.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> ReconnectRun:
        """Exclusive SQLite owner with renewal and transactional ACK fencing.

        This cannot kill a stale WebSocket connection or fence side effects in
        another datastore. Source captures remain session scoped, offline only.
        """
        self.journal._simulation_only()
        fence = self.worker_fence
        if fence is None:
            raise ValueError("run_fenced_batch requires configured worker_fence")
        if (
            type(heartbeat_interval_s) not in (int, float)
            or not 0 < heartbeat_interval_s < fence.lease_ms / 2000
        ):
            raise ValueError("heartbeat interval must be below half the lease")
        claim = fence.acquire(holder_id=holder_id)
        state = {"claim": claim}
        worker: asyncio.Task[ReconnectRun] | None = None
        try:
            # Expired generations cannot commit in the same DB; only the
            # newly acquired owner may reconcile their unfinished attempts.
            self.reconcile_crashed_attempts(lease=state["claim"])
            worker = asyncio.create_task(
                self.run_batch(
                    capture_once, max_attempts=max_attempts,
                    backoff_s=backoff_s, sleep=sleep,
                    _lease_ref=lambda: state["claim"],
                ),
                name="spot-fenced-reconnect",
            )
            while True:
                done, _ = await asyncio.wait(
                    {worker}, timeout=heartbeat_interval_s,
                )
                if done:
                    return await worker
                state["claim"] = fence.renew(state["claim"])
        finally:
            if worker is not None and not worker.done():
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            try:
                fence.release(state["claim"])
            except (WorkerLeaseConflict, RuntimeError):
                # Lease was already lost or REALITY mode became active.
                pass

    async def run_fenced_public(
        self, adapter: OptInSpotIngressAdapter, *, holder_id: str,
        allow_network: bool = False, max_attempts: int = 3,
        backoff_s: float = 1.0, max_frames: int = 300,
        timeout_s: float = 30.0, heartbeat_interval_s: float = 5.0,
    ) -> ReconnectRun:
        """Explicit public, read-only market capture under durable ownership."""
        self.journal._simulation_only()
        if allow_network is not True:
            raise PermissionError("explicit public market-data opt-in required")
        if adapter.journal is not self.journal or adapter.symbol != self.symbol:
            raise ValueError("adapter must share controller journal and symbol")

        async def _once(session_id: str, _sequence: int) -> SpotTransportResult:
            return await adapter.capture_public_once(
                allow_network=True, session_id=session_id,
                max_frames=max_frames, timeout_s=timeout_s,
            )

        return await self.run_fenced_batch(
            _once, holder_id=holder_id, max_attempts=max_attempts,
            backoff_s=backoff_s, heartbeat_interval_s=heartbeat_interval_s,
        )

    async def run_public(
        self, adapter: OptInSpotIngressAdapter, *,
        allow_network: bool = False,
        max_attempts: int = 3, backoff_s: float = 1.0,
        max_frames: int = 300, timeout_s: float = 30.0,
    ) -> ReconnectRun:
        """Explicit read-only public transport; never invoked by default."""
        self.journal._simulation_only()
        if allow_network is not True:
            raise PermissionError("explicit public market-data opt-in required")
        if adapter.journal is not self.journal or adapter.symbol != self.symbol:
            raise ValueError("adapter must share controller journal and symbol")

        async def _one(session_id: str, _attempt: int) -> SpotTransportResult:
            return await adapter.capture_public_once(
                allow_network=True, session_id=session_id,
                max_frames=max_frames, timeout_s=timeout_s,
            )

        return await self.run_batch(
            _one, max_attempts=max_attempts, backoff_s=backoff_s,
        )
