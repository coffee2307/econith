"""Phase 1 research-only market event integrity: no exchange or broker access.

This is an opt-in journal, NOT a drop-in market-data stream replacement.
Only Binance-style aggregate trade IDs support the +1 sequence policy here.
Partial depth lastUpdateId is NOT a contiguous stream and is not admitted.
Input source/market identity is caller-asserted, not exchange-authenticated.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

from core.mode import QuantMode

_SCOPE_PART = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SYMBOL = re.compile(r"^[A-Z0-9]{3,20}$")
Status = Literal[
    "ACCEPTED", "DUPLICATE", "CONFLICT", "GAP", "OUT_OF_ORDER",
    "STALE", "FUTURE", "INVALID",
]


class MarketEventError(ValueError):
    """Malformed or mismatched provenance; never release to research consumers."""


@dataclass(frozen=True)
class MarketIngestResult:
    status: Status
    event_id: str
    sequence: int
    available_at_ms: int
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "ACCEPTED"


@dataclass(frozen=True)
class StoredMarketEvent:
    event_id: str
    scope: str
    sequence: int
    event_ms: int
    trade_ms: int
    available_at_ms: int
    frame: dict[str, Any]


class OfflineMarketIntegrityJournal:
    """Durably gate one aggregate-trade sequence per isolated market/source/symbol.

    Accepted events form a strictly contiguous sequence FROM THE FIRST
    OBSERVED BASELINE; earlier history is unverified. Gaps are recorded as
    incidents but DO NOT advance the cursor. The missing frames must be
    ingested before later frames are eligible. Replays use receipt time
    (available_at_ms), never exchange event time, to prevent look-ahead.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        market: str = "binance_spot",
        mode_provider: Callable[[], QuantMode],
        max_age_ms: int = 10_000,
        max_future_skew_ms: int = 1_000,
    ) -> None:
        if not _SCOPE_PART.fullmatch(market):
            raise ValueError("explicit market scope required")
        if not callable(mode_provider):
            raise ValueError("explicit simulation mode provider required")
        if (
            not isinstance(max_age_ms, int) or isinstance(max_age_ms, bool)
            or max_age_ms < 0
            or not isinstance(max_future_skew_ms, int)
            or isinstance(max_future_skew_ms, bool)
            or max_future_skew_ms < 0
        ):
            raise ValueError("invalid age or future clock tolerance")
        self.path = Path(path)
        self.market = market
        self._mode = mode_provider
        self.max_age_ms = max_age_ms
        self.max_future_skew_ms = max_future_skew_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_events (
                    ingest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    scope TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_ms INTEGER NOT NULL,
                    trade_ms INTEGER NOT NULL,
                    available_at_ms INTEGER NOT NULL,
                    frame_json TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    UNIQUE(scope,sequence)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_cursors (
                    scope TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_market_incidents (
                    incident_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    detail TEXT NOT NULL
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            yield conn
        finally:
            conn.close()

    def _simulation_only(self) -> None:
        if self._mode() is not QuantMode.SIMULATION:
            raise RuntimeError("market integrity research strictly SIMULATION-only")

    @staticmethod
    def _positive_int(value: Any, field: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise MarketEventError(f"{field} must be a positive integer")
        return value

    @classmethod
    def _validate(cls, frame: Mapping[str, Any]) -> tuple[str, int, int, int, str, str]:
        if not isinstance(frame, Mapping) or frame.get("e") != "aggTrade":
            raise MarketEventError("only explicit aggTrade frames supported")
        symbol = frame.get("s")
        if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
            raise MarketEventError("invalid source instrument symbol")
        sequence = cls._positive_int(frame.get("a"), "aggregate trade ID")
        event_ms = cls._positive_int(frame.get("E"), "exchange event time")
        trade_ms = cls._positive_int(frame.get("T"), "exchange trade time")
        first = cls._positive_int(frame.get("f"), "first trade ID")
        last = cls._positive_int(frame.get("l"), "last trade ID")
        if first > last or type(frame.get("m")) is not bool:
            raise MarketEventError("invalid constituent trade IDs or aggressor flag")
        for key in ("p", "q"):
            value = frame.get(key)
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise MarketEventError(f"invalid {key}")
            try:
                numeric = Decimal(str(value))
            except InvalidOperation as exc:
                raise MarketEventError(f"invalid {key}") from exc
            if not numeric.is_finite() or numeric <= 0:
                raise MarketEventError(f"nonpositive/nonfinite {key}")
        try:
            serial = json.dumps(
                dict(frame), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise MarketEventError("non-JSON or non-finite market frame") from exc
        return symbol, sequence, event_ms, trade_ms, serial, hashlib.sha256(
            serial.encode("utf-8")
        ).hexdigest()

    def ingest_agg_trade(
        self,
        frame: Mapping[str, Any],
        *,
        source: str,
        received_at_ms: int | None = None,
    ) -> MarketIngestResult:
        """Serialize dedup, sequence and point-in-time admission in ONE transaction.

        The optional explicit received_at_ms is for deterministic offline replay
        and testing; a production ingestor must use a trusted local receipt
        clock and may NOT backdate the availability of a late packet.
        """
        self._simulation_only()
        if not isinstance(source, str) or not _SCOPE_PART.fullmatch(source):
            raise MarketEventError("invalid explicit data source")
        symbol, seq, event_ms, trade_ms, serial, digest = self._validate(frame)
        now = int(time.time() * 1000) if received_at_ms is None else received_at_ms
        self._positive_int(now, "received_at_ms")
        scope = f"{self.market}:{source}:{symbol}:aggTrade"
        event_id = f"md:{scope}:{seq}"
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                current = conn.execute(
                    "SELECT * FROM offline_market_events WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if current is not None:
                    if current["fingerprint"] != digest or current["frame_json"] != serial:
                        outcome: Status = "CONFLICT"
                        detail = "same market ID with changed immutable frame"
                        self._incident(conn, event_id, outcome, now, digest, detail)
                    else:
                        outcome = "DUPLICATE"
                        detail = "same immutable upstream event replayed"
                    conn.execute("COMMIT")
                    return MarketIngestResult(outcome, event_id, seq, current["available_at_ms"], detail)

                cursor = conn.execute(
                    "SELECT last_sequence FROM offline_market_cursors WHERE scope=?",
                    (scope,),
                ).fetchone()
                last_seq = cursor["last_sequence"] if cursor is not None else None
                if last_seq is not None and seq <= last_seq:
                    outcome, detail = "OUT_OF_ORDER", "sequence at/below durable cursor but event absent"
                elif last_seq is not None and seq > last_seq + 1:
                    outcome, detail = "GAP", f"expected {last_seq + 1}, received {seq}"
                elif event_ms > now + self.max_future_skew_ms or trade_ms > now + self.max_future_skew_ms:
                    outcome, detail = "FUTURE", "exchange timestamp exceeds receipt clock tolerance"
                elif event_ms - trade_ms > self.max_age_ms or now - min(event_ms, trade_ms) > self.max_age_ms:
                    outcome, detail = "STALE", "market event was too old or trade timestamp lagged"
                else:
                    outcome, detail = "ACCEPTED", "local sequence and receipt-time checks passed"

                if outcome != "ACCEPTED":
                    self._incident(conn, event_id, outcome, now, digest, detail)
                    conn.execute("COMMIT")
                    return MarketIngestResult(outcome, event_id, seq, now, detail)

                conn.execute(
                    """INSERT INTO offline_market_events
                       (event_id,scope,sequence,event_ms,trade_ms,available_at_ms,
                        frame_json,fingerprint) VALUES (?,?,?,?,?,?,?,?)""",
                    (event_id, scope, seq, event_ms, trade_ms, now, serial, digest),
                )
                if last_seq is None:
                    conn.execute(
                        "INSERT INTO offline_market_cursors(scope,last_sequence) VALUES(?,?)",
                        (scope, seq),
                    )
                else:
                    conn.execute(
                        "UPDATE offline_market_cursors SET last_sequence=? WHERE scope=?",
                        (seq, scope),
                    )
                conn.execute("COMMIT")
                return MarketIngestResult("ACCEPTED", event_id, seq, now, detail)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _incident(
        conn: sqlite3.Connection, event_id: str, status: str,
        received: int, digest: str, detail: str,
    ) -> None:
        conn.execute(
            """INSERT INTO offline_market_incidents
               (event_id,status,received_at_ms,fingerprint,detail)
               VALUES (?,?,?,?,?)""",
            (event_id, status, received, digest, detail),
        )

    def replay_as_of(self, cutoff_ms: int, *, limit: int = 1000) -> list[StoredMarketEvent]:
        """Return only committed events observable by receipt cutoff in ingest order."""
        self._simulation_only()
        self._positive_int(cutoff_ms, "cutoff")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 5000:
            raise ValueError("invalid replay limit")
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM offline_market_events
                   WHERE available_at_ms <= ?
                   ORDER BY ingest_id LIMIT ?""",
                (cutoff_ms, limit),
            ).fetchall()
        result = []
        for row in rows:
            frame = json.loads(row["frame_json"])
            symbol, seq, event_ms, trade_ms, serial, digest = self._validate(frame)
            if (
                digest != row["fingerprint"]
                or serial != row["frame_json"]
                or row["event_id"] != f"md:{row['scope']}:{seq}"
                or row["sequence"] != seq
                or row["event_ms"] != event_ms
                or row["trade_ms"] != trade_ms
                or f":{symbol}:aggTrade" not in row["scope"]
            ):
                raise MarketEventError("durable replay record failed integrity verification")
            result.append(StoredMarketEvent(
                event_id=row["event_id"], scope=row["scope"], sequence=seq,
                event_ms=event_ms, trade_ms=trade_ms,
                available_at_ms=row["available_at_ms"], frame=frame,
            ))
        return result

    def unresolved_incidents(self) -> list[dict[str, Any]]:
        """Read-only audit list; incidents are never auto-ignored or downgraded."""
        self._simulation_only()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT event_id,status,received_at_ms,detail
                   FROM offline_market_incidents ORDER BY incident_id"""
            ).fetchall()
        return [dict(r) for r in rows]
