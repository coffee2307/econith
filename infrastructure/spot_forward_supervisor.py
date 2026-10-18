"""Bounded, durable Spot forward research supervisor with honest outage ledger.

This is an explicit, finite, read-only multi-session operator trial, not an
uninterrupted market stream and not production deployment. Each attempt uses
a fresh public WebSocket and REST snapshot, fresh original source session and
its own outbox/independent HMAC receiver/derived observer databases.

Durable attempt STARTED is written before any network or callback. A process
hard-exit leaves STARTED plus an orphan owner lock: never auto-resume an
uncertain source or overwrite earlier evidence. A failed attempt opens an
auditable outage; a later complete attempt marks RESTORED_CURRENT_BOOK,
not recovered trade history. Even consecutive successful finite attempts
contain an unobserved interval and NEVER imply historical continuity.
"""
from __future__ import annotations

import asyncio
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import OfflineIndependentProjectionReceiver
from infrastructure.market_derived_research_receipts import OfflineDerivedResearchReceiptLedger
from infrastructure.market_named_research_subscribers import OfflineNamedResearchSubscriber
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_owner import exclusive_forward_owner
from infrastructure.spot_forward_verified_research import (
    ForwardSpotResearchStatus, observe_forward_spot_research,
)
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal

_SOURCE = "verified_forward_spot_shadow"
_TOPICS = ("md.ticker", "indicator.volume_delta", "indicator.obi")


class ForwardSupervisorError(RuntimeError):
    """Missing safety policy, interrupted attempt or mismatched source ACK."""


@dataclass(frozen=True)
class ForwardSupervisorStatus:
    mode: str
    state: str
    attempts: int
    accepted_sessions: int
    failed_sessions: int
    incomplete_attempts: int
    outage_windows: int
    open_outages: int
    total_originals: int
    total_receiver_receipts: int
    max_queue_high_watermark: int
    peak_recorded_storage_bytes: int
    receiver_process_starts: int = 0
    max_receiver_ack_latency_ms: float = 0.0
    planned_watch_duration_s: float | None = None
    observed_watch_duration_s: float = 0.0
    watch_target_reached: bool = False
    unobserved_session_boundaries: int = 0
    historical_gaps_recovered: bool = False
    history_continuity_verified: bool = False
    continuous_live_market: bool = False
    execution_started: bool = False
    orders_submitted: int = 0


def _clock() -> int:
    return time.time_ns() // 1_000_000


def _storage_bytes(root: Path) -> int:
    # Do not follow symlinks while measuring approved research evidence.
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink())


def _verify_one(root: Path, result: ForwardSpotResearchStatus) -> None:
    """Reopen original, producer, receiver and three independently owned ACKs."""
    if (
        not isinstance(result, ForwardSpotResearchStatus)
        or result.mode != "SIMULATION"
        or not result.session_complete
        or not result.all_receipts_independent
        or not result.research_only
        or not result.publication_in_session
        or result.continuous_live_market
        or result.history_continuity_verified
        or result.execution_started
        or result.orders_submitted != 0
        or result.queue_high_watermark < 0
        or result.queue_high_watermark > 128
        or result.receiver_process_starts < 0
        or (result.receiver_transport == "persistent_hmac"
            and result.receiver_process_starts != 1)
        or result.max_receiver_ack_latency_ms < 0
        or result.original_records <= 0
        or result.forwarded_trades <= 0
        or result.forwarded_books <= 0
    ):
        raise ForwardSupervisorError("untrusted forward session claim")
    required = [
        "original.sqlite", "forward_outbox.sqlite",
        "independent_receiver.sqlite", "derived.sqlite",
        "research_audit.sqlite", "research_monitor.sqlite",
    ]
    if any(
        not (root / filename).is_file() or (root / filename).is_symlink()
        for filename in required
    ):
        raise ForwardSupervisorError("incomplete independent durable evidence")
    provider = lambda: QuantMode.SIMULATION
    original = OfflineSpotCaptureJournal(
        root/"original.sqlite",mode_provider=provider,forward_capture_audit=True,
    )
    source_rows = original.verify_forward_capture_inventory(
        result.session_id, limit=5000,
    )
    outbox = OfflineForwardSpotOutbox(
        root/"forward_outbox.sqlite",
        session_id=result.session_id,mode_provider=provider,
    )
    intents = outbox.verify_source(original,source_session_id=result.session_id)
    receiver = OfflineIndependentProjectionReceiver(
        root/"independent_receiver.sqlite",mode_provider=provider,
    )
    receipts = receiver.receipts("forward_spot","read_only_market")
    # Counts alone do not authenticate WHICH original each independent
    # receiver acknowledged. Compare every receipt to its immutable intent.
    exact = {row.envelope.ingest_id:row.envelope for row in intents}
    if any(
        r.ingest_id not in exact
        or r.event_id != exact[r.ingest_id].event_id
        or r.source_consumer != exact[r.ingest_id].source_consumer
        or r.receiver != exact[r.ingest_id].receiver
        or r.projection_fingerprint != exact[r.ingest_id].projection_fingerprint
        or r.receipt_id != OfflineIndependentProjectionReceiver._receipt_id(
            exact[r.ingest_id]
        )
        or r.received_at_ms < exact[r.ingest_id].available_at_ms
        for r in receipts
    ):
        raise ForwardSupervisorError("independent receipt does not match source intent")
    with original._connect() as conn:
        session = conn.execute(
            "SELECT closed_at_ms,symbol FROM offline_spot_capture_sessions "
            "WHERE session_id=?", (result.session_id,),
        ).fetchone()
    if session is None or session["closed_at_ms"] is None or session["symbol"]!="BTCUSDT":
        raise ForwardSupervisorError("unclosed or unrelated original session")
    counts = {
        "md.ticker":result.forwarded_trades,
        "indicator.volume_delta":result.forwarded_trades,
        "indicator.obi":result.forwarded_books,
    }
    primary = OfflineDerivedResearchReceiptLedger(
        root/"derived.sqlite",mode_provider=provider,
    )
    acks = {t:primary.verified_count(result.session_id,t) for t in _TOPICS}
    named = [
        OfflineNamedResearchSubscriber(
            root/(name+".sqlite"),name=name,
            expected_source=_SOURCE,mode_provider=provider,
        ).verify(result.session_id, counts)
        for name in ("research_audit","research_monitor")
    ]
    if (
        len(source_rows) != result.original_records
        or len(intents) != result.forwarded_trades + result.forwarded_books
        or any(i.status != "DERIVED_ACKED" for i in intents)
        or len(receipts) != result.receiver_receipts
        or len({r.ingest_id for r in receipts}) != len(receipts)
        or len(intents) != len(receipts)
        or acks != counts
        or sum(acks.values()) != result.primary_derived_receipts
        or sum(x.durable_receipts for x in named) != result.named_derived_receipts
    ):
        raise ForwardSupervisorError("original/outbox/receiver/derived proof diverged")


def _initialize(root: Path, *, watch_duration_s: float | None = None,
                planned_attempts: int = 3,
                storage_ceiling_bytes: int = 100_000_000) -> Path:
    path = root / "forward_supervisor.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("""
            CREATE TABLE forward_watch_attempt (
                iteration INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL UNIQUE,
                started_ms INTEGER NOT NULL,
                finished_ms INTEGER,
                status TEXT NOT NULL CHECK(status IN ('STARTED','PASSED','FAILED')),
                failure_code TEXT,
                originals INTEGER NOT NULL DEFAULT 0,
                receiver_acks INTEGER NOT NULL DEFAULT 0,
                queue_high_watermark INTEGER NOT NULL DEFAULT 0,
                recorded_storage_bytes INTEGER NOT NULL DEFAULT 0,
                receiver_process_starts INTEGER NOT NULL DEFAULT 0,
                max_receiver_ack_latency_ms REAL NOT NULL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE forward_watch_outage (
                outage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_ms INTEGER NOT NULL,
                restored_ms INTEGER,
                last_failure_code TEXT NOT NULL,
                coverage_verified INTEGER NOT NULL DEFAULT 0 CHECK(coverage_verified=0)
            )
        """)
        db.execute("""
            CREATE TABLE forward_watch_plan (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                planned_watch_duration_s REAL,
                attempts_ceiling INTEGER NOT NULL,
                storage_ceiling_bytes INTEGER NOT NULL,
                admitted_at_ms INTEGER NOT NULL,
                completed_at_ms INTEGER,
                completion_code TEXT
            )
        """)
        db.execute("""
            INSERT INTO forward_watch_plan
            (singleton,planned_watch_duration_s,attempts_ceiling,
             storage_ceiling_bytes,admitted_at_ms)
            VALUES (1,?,?,?,?)
        """,(watch_duration_s,planned_attempts,storage_ceiling_bytes,_clock()))
        db.execute("""
            CREATE TABLE forward_watch_unobserved_boundary (
                boundary_id INTEGER PRIMARY KEY AUTOINCREMENT,
                previous_iteration INTEGER NOT NULL,
                next_iteration INTEGER NOT NULL,
                previous_finished_ms INTEGER NOT NULL,
                next_started_ms INTEGER NOT NULL,
                coverage_verified INTEGER NOT NULL DEFAULT 0 CHECK(coverage_verified=0),
                UNIQUE(previous_iteration,next_iteration)
            )
        """)
    return path


def _summarize(db_path: Path, root: Path,
               *, observed_watch_s: float = 0) -> ForwardSupervisorStatus:
    with sqlite3.connect(db_path) as db:
        attempts=db.execute("""
            SELECT COUNT(*), SUM(status='PASSED'),SUM(status='FAILED'),
                   SUM(status='STARTED'),COALESCE(SUM(originals),0),
                   COALESCE(SUM(receiver_acks),0),
                   COALESCE(MAX(queue_high_watermark),0),
                   COALESCE(MAX(recorded_storage_bytes),0),
                   COALESCE(SUM(receiver_process_starts),0),
                   COALESCE(MAX(max_receiver_ack_latency_ms),0)
            FROM forward_watch_attempt
        """).fetchone()
        outage=db.execute("""
            SELECT COUNT(*),COALESCE(SUM(restored_ms IS NULL),0)
            FROM forward_watch_outage
        """).fetchone()
        boundary_count=db.execute("""
            SELECT COUNT(*) FROM forward_watch_unobserved_boundary
            WHERE coverage_verified=0
        """).fetchone()[0]
        plan=db.execute("""
            SELECT planned_watch_duration_s,completion_code
            FROM forward_watch_plan WHERE singleton=1
        """).fetchone()
    status=("INCOMPLETE" if attempts[3] else
            "WATCH_TARGET_REACHED_WITH_UNKNOWN_GAPS" if
            plan[1]=="TARGET_REACHED" else
            "WATCH_ATTEMPT_CEILING_REACHED" if plan[0] is not None else
            "FINISHED_BOUNDED_RESEARCH")
    return ForwardSupervisorStatus(
        mode="SIMULATION", state=status,
        attempts=attempts[0],accepted_sessions=attempts[1] or 0,
        failed_sessions=attempts[2] or 0,incomplete_attempts=attempts[3] or 0,
        outage_windows=outage[0],open_outages=outage[1],
        total_originals=attempts[4],total_receiver_receipts=attempts[5],
        max_queue_high_watermark=attempts[6],
        peak_recorded_storage_bytes=max(attempts[7],_storage_bytes(root)),
        receiver_process_starts=attempts[8],
        max_receiver_ack_latency_ms=attempts[9],
        planned_watch_duration_s=plan[0],
        observed_watch_duration_s=observed_watch_s,
        watch_target_reached=plan[1]=="TARGET_REACHED",
        unobserved_session_boundaries=boundary_count,
    )


async def run_forward_research_supervisor(
    *, output_dir: Path, allow_public_network: bool = False,
    attempts: int = 3, max_frames: int = 160,
    timeout_s: float = 45, pause_s: float = 0,
    max_total_storage_bytes: int = 100_000_000,
    max_book_staleness_ms: int = 2500,
    persistent_receiver: bool = False,
    watch_duration_s: float | None = None,
    _monotonic_s: Callable[[],float] | None = None,
    clock_check: Callable[[], Awaitable[bool]] | None = None,
    capture_factory: Callable[
        [Path,str,int], Awaitable[ForwardSpotResearchStatus]
    ] | None = None,
) -> ForwardSupervisorStatus:
    """Finite forward test with durable outage windows and per-session proof.

    Real public mode requires clock preflight on EVERY attempt. A captured
    exception is recorded as FAILED, not accidentally marked an ACK or
    evidence of continuous market history.
    """
    if allow_public_network is not True:
        raise PermissionError("explicit public read-only research opt-in required")
    if (
        not isinstance(output_dir,Path) or not output_dir.is_absolute()
        or output_dir.exists() or output_dir != output_dir.resolve()
        or output_dir.parent.is_symlink()
        or type(attempts) is not int or not 1 <= attempts <= (
            4096 if watch_duration_s is not None else 16)
        or type(max_frames) is not int or not 10 <= max_frames <= 2000
        or attempts*max_frames > (
            4_096_000 if watch_duration_s is not None else 32000)
        or (watch_duration_s is not None and (
            type(watch_duration_s) not in (int,float)
            or not 5 <= watch_duration_s <= 259200
            or not persistent_receiver))
        or (_monotonic_s is not None and (
            not callable(_monotonic_s) or capture_factory is None))
        or type(timeout_s) not in (int,float) or not 5 <= timeout_s <= 120
        or type(pause_s) not in (int,float) or not 0 <= pause_s <= 30
        or type(max_total_storage_bytes) is not int
        or not 2_000_000 <= max_total_storage_bytes <= 2_000_000_000
        or type(max_book_staleness_ms) is not int
        or not 100 <= max_book_staleness_ms <= 30000
        or type(persistent_receiver) is not bool
    ):
        raise ValueError("unsafe bounded forward supervision configuration")
    if capture_factory is None and not callable(clock_check):
        raise ForwardSupervisorError("public clock check required for each socket")
    if capture_factory is not None and not callable(capture_factory):
        raise ValueError("capture factory must be callable")
    output_dir.mkdir(parents=True,exist_ok=False)
    monotonic_fn=_monotonic_s or time.monotonic
    start_monotonic=monotonic_fn()
    with exclusive_forward_owner(output_dir,role="CAPTURE"):
        db_path = _initialize(
            output_dir,watch_duration_s=watch_duration_s,
            planned_attempts=attempts,
            storage_ceiling_bytes=max_total_storage_bytes,
        )
        # Append-only session rows survive a hard process exit. Such an
        # interrupted run cannot be silently resumed over partial evidence.
        for iteration in range(1, attempts+1):
            if _storage_bytes(output_dir) > max_total_storage_bytes:
                raise ForwardSupervisorError("durable storage ceiling reached before admission")
            sid = "rec_forward_" + secrets.token_hex(12)
            directory=output_dir/f"attempt_{iteration:03d}"
            started=_clock()
            with sqlite3.connect(db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                previous=db.execute("""
                    SELECT iteration,finished_ms FROM forward_watch_attempt
                    WHERE iteration=? AND status IN ('PASSED','FAILED')
                """,(iteration-1,)).fetchone()
                if iteration>1 and previous is None:
                    raise ForwardSupervisorError("previous source not durably closed")
                if previous is not None:
                    # Even successful successive source windows have unknowable
                    # exchange observations between the two receipt clocks.
                    db.execute("""
                        INSERT INTO forward_watch_unobserved_boundary
                        (previous_iteration,next_iteration,
                         previous_finished_ms,next_started_ms,coverage_verified)
                        VALUES (?,?,?,?,0)
                    """,(previous[0],iteration,previous[1],started))
                db.execute(
                    """INSERT INTO forward_watch_attempt
                    (iteration,session_id,started_ms,status)
                    VALUES (?,?,?,'STARTED')""",
                    (iteration,sid,started),
                )
                db.execute("COMMIT")
            try:
                if clock_check is not None and await clock_check() is not True:
                    raise ForwardSupervisorError("public clock admission refused")
                if capture_factory is None:
                    result=await observe_forward_spot_research(
                        output_dir=directory,session_id=sid,
                        allow_public_network=True,
                        source="binance_public_simulation",
                        max_frames=max_frames,timeout_s=timeout_s,
                        max_book_staleness_ms=max_book_staleness_ms,
                        persistent_receiver=persistent_receiver,
                    )
                else:
                    result=await capture_factory(directory,sid,iteration)
                if result.session_id != sid:
                    raise ForwardSupervisorError("cross-session proof substituted")
                _verify_one(directory,result)
                size=_storage_bytes(output_dir)
                if size > max_total_storage_bytes:
                    raise ForwardSupervisorError("durable storage ceiling exceeded")
            except asyncio.CancelledError:
                # Preserve explicit durable interrupted state for operators.
                raise
            except Exception as exc:
                failure=type(exc).__name__
                if len(failure)>70 or not failure.isidentifier():
                    failure="FORWARD_SOURCE_ERROR"
                finished=_clock()
                with sqlite3.connect(db_path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""
                        UPDATE forward_watch_attempt
                        SET status='FAILED', finished_ms=?,failure_code=?
                        WHERE iteration=? AND status='STARTED'
                    """,(finished,failure,iteration))
                    active=db.execute("""
                        SELECT outage_id FROM forward_watch_outage
                        WHERE restored_ms IS NULL
                    """).fetchone()
                    if active is None:
                        db.execute("""
                            INSERT INTO forward_watch_outage
                            (opened_ms,last_failure_code,coverage_verified)
                            VALUES (?,?,0)
                        """,(started,failure))
                    else:
                        db.execute("""
                            UPDATE forward_watch_outage SET last_failure_code=?
                            WHERE outage_id=?
                        """,(failure,active[0]))
                    db.execute("COMMIT")
                # A storage/clock integrity violation cannot be retried.
                if isinstance(exc,ForwardSupervisorError) or (
                    _storage_bytes(output_dir) > max_total_storage_bytes
                ):
                    raise
            else:
                finished=_clock()
                with sqlite3.connect(db_path) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("""
                        UPDATE forward_watch_attempt SET
                        status='PASSED',finished_ms=?,originals=?,
                        receiver_acks=?,queue_high_watermark=?,
                        recorded_storage_bytes=?,receiver_process_starts=?,
                        max_receiver_ack_latency_ms=?
                        WHERE iteration=? AND status='STARTED'
                    """,(
                        finished,result.original_records,
                        result.receiver_receipts,result.queue_high_watermark,
                        size,result.receiver_process_starts,
                        result.max_receiver_ack_latency_ms,iteration,
                    ))
                    db.execute("""
                        UPDATE forward_watch_outage SET restored_ms=?
                        WHERE restored_ms IS NULL
                    """,(finished,))
                    db.execute("COMMIT")
            if watch_duration_s is not None and (
                monotonic_fn()-start_monotonic >= watch_duration_s
            ):
                with sqlite3.connect(db_path) as db:
                    db.execute("""
                        UPDATE forward_watch_plan
                        SET completion_code='TARGET_REACHED',completed_at_ms=?
                        WHERE singleton=1
                    """,(_clock(),))
                break
            if iteration<attempts and pause_s:
                await asyncio.sleep(pause_s)
        if watch_duration_s is not None:
            with sqlite3.connect(db_path) as db:
                db.execute("""
                    UPDATE forward_watch_plan
                    SET completion_code=COALESCE(completion_code,'ATTEMPT_CEILING'),
                        completed_at_ms=COALESCE(completed_at_ms,?)
                    WHERE singleton=1
                """,(_clock(),))
        return _summarize(
            db_path,output_dir,observed_watch_s=max(0.0,
            monotonic_fn()-start_monotonic),
        )
