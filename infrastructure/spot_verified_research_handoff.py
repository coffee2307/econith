"""Explicit offline promotion of captured raw Spot trades to verified journal.

This adapter does not alter the legacy live EventBus pipeline. A session must
be CLOSED and reconstruct a synchronized order book by the requested as-of
before promoting any captured aggTrade. The downstream market journal retains
original first receipt clocks, contiguity and a durable cross-DB promotion
manifest. A crash between the market insert and manifest insert replays safely
because the source market journal is idempotent by immutable event identity.
"""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from infrastructure.market_data_integrity import OfflineMarketIntegrityJournal
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal, SpotCaptureError


@dataclass(frozen=True)
class PromotedTrade:
    session_id: str
    capture_id: int
    event_id: str
    available_at_ms: int
    status: str


@dataclass(frozen=True)
class PromotionBatch:
    session_id: str
    as_of_ms: int
    book_last_update_id: int
    admitted: int
    duplicates: int
    records: tuple[PromotedTrade, ...]


class OfflineSpotVerifiedResearchHandoff:
    """Non-default, deterministic opt-in bridge between independent journals.

    Only immutable original trades from an already closed, verified and
    synchronized capture can be admitted; no snapshot or depth events get
    misrepresented as aggTrades. This does not authenticate source labels or
    certify the wall clock or that a market interval was fully observed.
    """

    def __init__(
        self, capture: OfflineSpotCaptureJournal,
        destination: OfflineMarketIntegrityJournal,
    ) -> None:
        if not isinstance(capture, OfflineSpotCaptureJournal):
            raise ValueError("Spot capture journal required")
        if not isinstance(destination, OfflineMarketIntegrityJournal):
            raise ValueError("verified trade integrity journal required")
        capture._simulation_only()
        destination._simulation_only()
        if capture.path.resolve() == destination.path.resolve():
            raise ValueError("market and original capture require separate SQLite files")
        if destination.market != "binance_spot":
            raise ValueError("only the canonical Binance Spot integrity scope permitted")
        self.capture, self.destination = capture, destination
        with destination._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_promotion_manifest (
                    capture_session_id TEXT NOT NULL,
                    capture_id INTEGER NOT NULL CHECK(capture_id > 0),
                    event_id TEXT NOT NULL,
                    source_available_at_ms INTEGER NOT NULL,
                    raw_sha256 TEXT NOT NULL,
                    PRIMARY KEY(capture_session_id,capture_id)
                )
            """)

    def promote_session(
        self, session_id: str, *, as_of_ms: int, limit: int = 5000,
    ) -> PromotionBatch:
        self.capture._simulation_only()
        self.destination._simulation_only()
        self.capture._part(session_id, "session_id")
        self.capture._positive(as_of_ms, "as_of_ms")
        with self.capture._connect() as conn:
            session = conn.execute("""
                SELECT * FROM offline_spot_capture_sessions WHERE session_id=?
            """, (session_id,)).fetchone()
            if session is None or session["closed_at_ms"] is None:
                raise SpotCaptureError("promotion requires a closed original capture session")
            if as_of_ms < session["closed_at_ms"]:
                raise SpotCaptureError("promotion cannot precede capture session close")
        replay = OfflineSpotCaptureReplayer(self.capture).replay(
            session_id, as_of_ms=as_of_ms, limit=limit,
        )
        if replay.book is None or replay.depth_status != "SYNCED":
            raise SpotCaptureError("promotion refuses unverified/unsynchronized Spot book")
        recorded = []
        inserted = repeated = 0
        for trade in replay.trades:
            digest = hashlib.sha256(trade.raw).hexdigest()
            # These receipt stamps were persisted on ORIGINAL raw capture,
            # not exchange event time and not later replay/promotion time.
            result = self.destination.ingest_agg_trade(
                trade.payload, source=trade.source,
                received_at_ms=trade.received_at_ms,
            )
            if result.status not in ("ACCEPTED", "DUPLICATE"):
                raise SpotCaptureError(
                    f"verified market journal rejected source event: {result.status}"
                )
            if result.available_at_ms > trade.received_at_ms:
                raise SpotCaptureError("market event availability is later than first capture")
            with self.destination._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    previous = conn.execute("""
                        SELECT * FROM offline_spot_promotion_manifest
                        WHERE capture_session_id=? AND capture_id=?
                    """, (session_id, trade.capture_id)).fetchone()
                    if previous is not None:
                        if (
                            previous["event_id"] != result.event_id
                            or previous["source_available_at_ms"] != trade.received_at_ms
                            or previous["raw_sha256"] != digest
                        ):
                            raise SpotCaptureError("conflicting prior capture promotion mapping")
                    else:
                        conn.execute("""
                            INSERT INTO offline_spot_promotion_manifest
                            (capture_session_id,capture_id,event_id,
                             source_available_at_ms,raw_sha256)
                            VALUES (?,?,?,?,?)
                        """, (session_id, trade.capture_id, result.event_id,
                              trade.received_at_ms, digest))
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            recorded.append(PromotedTrade(
                session_id, trade.capture_id, result.event_id,
                result.available_at_ms, result.status,
            ))
            if result.status == "ACCEPTED":
                inserted += 1
            else:
                repeated += 1
        return PromotionBatch(
            session_id, as_of_ms, replay.book.last_update_id,
            inserted, repeated, tuple(recorded),
        )

    def manifest(self, session_id: str) -> list[PromotedTrade]:
        self.capture._simulation_only()
        self.destination._simulation_only()
        self.capture._part(session_id, "session_id")
        with self.destination._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM offline_spot_promotion_manifest
                WHERE capture_session_id=? ORDER BY capture_id
            """, (session_id,)).fetchall()
        out = []
        for row in rows:
            raw = self.capture.replay_as_of(session_id, 2**62, limit=5000)
            match = next((r for r in raw if r.capture_id == row["capture_id"]), None)
            if (
                match is None or match.kind != "aggTrade"
                or hashlib.sha256(match.raw).hexdigest() != row["raw_sha256"]
                or match.received_at_ms != row["source_available_at_ms"]
            ):
                raise SpotCaptureError("promotion manifest lost original source provenance")
            out.append(PromotedTrade(
                session_id, row["capture_id"], row["event_id"],
                row["source_available_at_ms"], "MANIFEST",
            ))
        return out
