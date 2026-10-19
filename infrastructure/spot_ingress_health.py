"""Read-only local Spot ingress health and durable projection backlog metrics.

No background monitoring, alarms, network requests or inference of exchange
completeness. Counts come from the opt-in research SQLite file only.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController
from infrastructure.spot_worker_fence import OfflineSpotWorkerFence


@dataclass(frozen=True)
class SpotIngressHealth:
    controller_id: str
    symbol: str
    checked_at_ms: int
    capture_sessions: int
    source_frames: int
    captured_bytes: int
    attempts_total: int
    attempts_success: int
    attempts_failed: int
    attempts_interrupted: int
    unfinished_attempts: int
    latest_success_at_ms: int | None
    open_outages: int
    unresolved_outage_age_ms: int | None
    worker_lease_active: bool | None
    worker_lease_generation: int | None
    unacknowledged_projections: int | None
    findings: tuple[str, ...]


class OfflineSpotIngressHealthMonitor:
    """Offline-on-demand observation, never a push notification or ACK.

    Projection backlog is counted for an explicit (source consumer, receiver)
    pair only. With neither pair nor initialized projection+inbox tables it is
    unknown (None), NEVER silently asserted to be zero.
    """

    def __init__(
        self, controller: OfflineSpotReconnectController, *,
        worker_fence: OfflineSpotWorkerFence | None = None,
    ) -> None:
        if not isinstance(controller, OfflineSpotReconnectController):
            raise ValueError("reconnect controller required")
        controller.journal._simulation_only()
        if worker_fence is not None and (
            not isinstance(worker_fence, OfflineSpotWorkerFence)
            or worker_fence.controller_id != controller.controller_id
            or worker_fence.journal.path.resolve()
                != controller.journal.path.resolve()
        ):
            raise ValueError("worker lease must share the same local controller")
        self.controller = controller
        self.worker_fence = worker_fence

    @staticmethod
    def _projection_backlog(
        conn: sqlite3.Connection, *, source_consumer: str,
        receiver: str, now: int,
    ) -> int | None:
        objects = {
            row["name"] for row in conn.execute("""
                SELECT name FROM sqlite_master WHERE type='table'
                  AND name IN (
                      'offline_market_projections',
                      'offline_market_projection_inbox_receipts'
                  )
            """)
        }
        if len(objects) != 2:
            return None
        return conn.execute("""
            SELECT COUNT(*) AS n FROM offline_market_projections p
            WHERE p.consumer=? AND p.available_at_ms<=?
            AND NOT EXISTS (
                SELECT 1 FROM offline_market_projection_inbox_receipts r
                WHERE r.source_consumer=p.consumer
                  AND r.ingest_id=p.ingest_id
                  AND r.receiver=?
            )
        """, (source_consumer, now, receiver)).fetchone()["n"]

    def snapshot(
        self, *, now_ms: int | None = None,
        max_recent_capture_age_ms: int = 60_000,
        max_outage_age_ms: int = 30_000,
        max_pending_projections: int = 100,
        source_consumer: str | None = None,
        receiver: str | None = None,
    ) -> SpotIngressHealth:
        self.controller.journal._simulation_only()
        journal = self.controller.journal
        now = self.controller._now() if now_ms is None else journal._positive(
            now_ms, "health observation clock",
        )
        for name, value in (
            ("max_recent_capture_age_ms", max_recent_capture_age_ms),
            ("max_outage_age_ms", max_outage_age_ms),
        ):
            if type(value) is not int or not 0 <= value <= 604_800_000:
                raise ValueError(f"invalid {name}")
        if type(max_pending_projections) is not int or not 1 <= max_pending_projections <= 10_000_000:
            raise ValueError("invalid max_pending_projections")
        if (source_consumer is None) != (receiver is None):
            raise ValueError("source_consumer and receiver must be supplied together")
        if source_consumer is not None:
            journal._part(source_consumer, "source_consumer")
            journal._part(receiver, "receiver")
        with journal._connect() as conn:
            s = conn.execute("""
                SELECT COUNT(DISTINCT s.session_id) AS sessions,
                       COUNT(r.capture_id) AS frames,
                       COALESCE(SUM(LENGTH(r.raw_blob)),0) AS total_bytes
                FROM offline_spot_capture_sessions s
                LEFT JOIN offline_spot_capture_records r
                    ON r.session_id=s.session_id
                WHERE s.symbol=?
            """, (self.controller.symbol,)).fetchone()
            a = conn.execute("""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) AS good,
                       SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN status='INTERRUPTED' THEN 1 ELSE 0 END) AS interrupted,
                       SUM(CASE WHEN status='STARTED' THEN 1 ELSE 0 END) AS started,
                       MAX(CASE WHEN status='SUCCESS'
                                THEN finished_at_ms END) AS last_good
                FROM offline_spot_reconnect_attempts WHERE controller_id=?
            """, (self.controller.controller_id,)).fetchone()
            outages = conn.execute("""
                SELECT started_at_ms
                FROM offline_spot_reconnect_outages
                WHERE controller_id=? AND restored_at_ms IS NULL
            """, (self.controller.controller_id,)).fetchall()
            backlog = self._projection_backlog(
                conn, source_consumer=source_consumer,
                receiver=receiver, now=now,
            ) if source_consumer is not None else None
        age = None if not outages else now - min(x["started_at_ms"] for x in outages)
        if age is not None and age < 0:
            raise ValueError("health clock precedes stored outage")
        last_good = a["last_good"]
        if last_good is not None and now < last_good:
            raise ValueError("health clock precedes last successful capture")
        active: bool | None = None
        generation: int | None = None
        if self.worker_fence is not None:
            lease = self.worker_fence.status(now_ms=now)
            active, generation = lease.active, lease.generation
        findings = []
        if a["total"] == 0 or last_good is None:
            findings.append("NO_VERIFIED_CAPTURE")
        elif now - last_good > max_recent_capture_age_ms:
            findings.append("LAST_VERIFIED_CAPTURE_STALE")
        if outages:
            findings.append("OPEN_OUTAGE")
        if age is not None and age > max_outage_age_ms:
            findings.append("OUTAGE_AGE_THRESHOLD")
        if a["started"]:
            findings.append("UNFINISHED_CAPTURE_ATTEMPT")
        if active is False and a["started"]:
            findings.append("NO_ACTIVE_FENCED_WORKER")
        if backlog is not None and backlog >= max_pending_projections:
            findings.append("PROJECTION_BACKLOG_THRESHOLD")
        if backlog is None:
            findings.append("PROJECTION_BACKLOG_UNKNOWN")
        return SpotIngressHealth(
            controller_id=self.controller.controller_id,
            symbol=self.controller.symbol, checked_at_ms=now,
            capture_sessions=s["sessions"], source_frames=s["frames"],
            captured_bytes=s["total_bytes"],
            attempts_total=a["total"], attempts_success=a["good"] or 0,
            attempts_failed=a["failed"] or 0,
            attempts_interrupted=a["interrupted"] or 0,
            unfinished_attempts=a["started"] or 0,
            latest_success_at_ms=last_good,
            open_outages=len(outages), unresolved_outage_age_ms=age,
            worker_lease_active=active, worker_lease_generation=generation,
            unacknowledged_projections=backlog,
            findings=tuple(findings),
        )
