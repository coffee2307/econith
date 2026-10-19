"""Research-only immutable Binance Spot raw-capture evidence with receipt clocks.

Opt-in SQLite journal for *original* combined-stream WebSocket envelopes and
read-only REST depth snapshots. All transport URLs, source claims, clock inputs
and session IDs are caller assertions, NOT TLS verification or signed origin.
No network, EventBus or execution code is reachable from this module.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping

from core.mode import QuantMode
from infrastructure.market_data_integrity import (
    MarketEventError, OfflineMarketIntegrityJournal,
)

_SYMBOL = re.compile(r"^[A-Z0-9]{3,20}$")
_PART = re.compile(r"^[A-Za-z0-9_-]{1,48}$")
_WS_ORIGINS = frozenset((
    "wss://stream.binance.com:9443/stream",
    "wss://stream.binance.com:443/stream",
    "wss://data-stream.binance.vision/stream",
))
_REST_DEPTH = frozenset((
    "https://api.binance.com/api/v3/depth",
    "https://data-api.binance.vision/api/v3/depth",
))


class SpotCaptureError(ValueError):
    """Failed session, payload, temporal or evidence-integrity condition."""


@dataclass(frozen=True)
class CapturedSpotRecord:
    capture_id: int
    session_id: str
    symbol: str
    source: str
    kind: str
    received_at_ms: int
    request_started_at_ms: int | None
    endpoint: str
    raw: bytes
    payload: dict[str, Any]


def _unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise SpotCaptureError("duplicate JSON key in raw transport body")
        result[key] = value
    return result


def _bad_number(value: str) -> Any:
    raise SpotCaptureError("non-finite JSON constant is forbidden")


class OfflineSpotCaptureJournal:
    """One durable capture per accepted original received frame, append order.

    Capture failures raise instead of silently dropping a frame. An upstream
    connection must stop accepting/releasing events if this journal rejects a
    write, including SQLite/database-full errors. Offline timestamp overrides
    are for deterministic fixtures ONLY and do not attest wall-clock time.
    """

    def __init__(
        self, path: str | Path, *, mode_provider: Callable[[], QuantMode],
        max_body_bytes: int = 262_144, max_frames_per_session: int = 100_000,
        max_age_ms: int = 10_000, max_future_skew_ms: int = 1000,
        forward_capture_audit: bool = False,
        reuse_connection: bool = False,
    ) -> None:
        if not callable(mode_provider):
            raise ValueError("SIMULATION-only mode provider is required")
        if (
            type(max_body_bytes) is not int or not 1024 <= max_body_bytes <= 4_194_304
            or type(max_frames_per_session) is not int
            or not 1 <= max_frames_per_session <= 1_000_000
            or type(max_age_ms) is not int or max_age_ms < 0
            or type(reuse_connection) is not bool
            or type(forward_capture_audit) is not bool
            or type(max_future_skew_ms) is not int or max_future_skew_ms < 0
        ):
            raise ValueError("invalid raw-capture resource bounds")
        self.path = Path(path)
        self._reuse_connection = reuse_connection
        self._session_connection: sqlite3.Connection | None = None
        self._mode_provider = mode_provider
        self.max_body_bytes = max_body_bytes
        self.max_frames_per_session = max_frames_per_session
        self.max_age_ms = max_age_ms
        self.max_future_skew_ms = max_future_skew_ms
        self.forward_capture_audit = forward_capture_audit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_capture_sessions (
                    session_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    source TEXT NOT NULL,
                    ws_endpoint TEXT NOT NULL,
                    opened_at_ms INTEGER NOT NULL,
                    closed_at_ms INTEGER,
                    last_receipt_ms INTEGER NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS offline_spot_capture_records (
                    capture_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    received_at_ms INTEGER NOT NULL,
                    request_started_at_ms INTEGER,
                    request_symbol TEXT,
                    endpoint TEXT NOT NULL,
                    raw_blob BLOB NOT NULL,
                    sha256 TEXT NOT NULL,
                    FOREIGN KEY(session_id)
                      REFERENCES offline_spot_capture_sessions(session_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS offline_capture_session_order
                ON offline_spot_capture_records(session_id,capture_id)
            """)
            if forward_capture_audit:
                # Optional forward-only write-ahead SOURCE inventory: raw row
                # and immutable receipt-intent proof commit in one transaction.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS offline_forward_capture_intents (
                        capture_id INTEGER PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        received_at_ms INTEGER NOT NULL,
                        sha256 TEXT NOT NULL,
                        FOREIGN KEY(capture_id)
                          REFERENCES offline_spot_capture_records(capture_id)
                    )
                """)

    from contextlib import contextmanager

    @contextmanager
    def _connect(self):
        # Explicit opt-in single-event-loop research ledger. Each original
        # plus its capture-intent still commits atomically with WAL/FULL.
        if self._reuse_connection:
            if self._session_connection is None:
                self._session_connection = sqlite3.connect(
                    self.path, isolation_level=None, timeout=10,
                )
                self._session_connection.row_factory = sqlite3.Row
                self._session_connection.execute("PRAGMA busy_timeout=10000")
                self._session_connection.execute("PRAGMA foreign_keys=ON")
            if self._session_connection.in_transaction:
                raise SpotCaptureError("cached original ledger has unfinished transaction")
            yield self._session_connection
        else:
            conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=10000")
                conn.execute("PRAGMA foreign_keys=ON")
                yield conn
            finally:
                conn.close()

    def close(self) -> None:
        db = self._session_connection
        if db is None:
            return
        self._session_connection = None
        try:
            if db.in_transaction:
                db.rollback()
                raise SpotCaptureError("uncommitted original ledger transaction on close")
        finally:
            db.close()

    def _simulation_only(self) -> None:
        if self._mode_provider() is not QuantMode.SIMULATION:
            raise RuntimeError("raw market capture strictly SIMULATION-only")

    @staticmethod
    def _positive(value: Any, name: str) -> int:
        if type(value) is not int or value <= 0:
            raise SpotCaptureError(f"{name} must be positive integer milliseconds")
        return value

    @staticmethod
    def _part(value: Any, name: str) -> str:
        if not isinstance(value, str) or _PART.fullmatch(value) is None:
            raise SpotCaptureError(f"invalid {name}")
        return value

    @staticmethod
    def _symbol(value: Any) -> str:
        if not isinstance(value, str) or _SYMBOL.fullmatch(value) is None:
            raise SpotCaptureError("invalid canonical Spot symbol")
        return value

    def _parse(self, raw: str | bytes) -> tuple[bytes, dict[str, Any]]:
        if type(raw) is str:
            body = raw.encode("utf-8")
        elif type(raw) is bytes:
            body = raw
        else:
            raise SpotCaptureError("raw body must be UTF-8 bytes or text")
        if not body or len(body) > self.max_body_bytes:
            raise SpotCaptureError("raw body size exceeds accepted bounds")
        try:
            parsed = json.loads(
                body.decode("utf-8"), object_pairs_hook=_unique_pairs,
                parse_constant=_bad_number,
            )
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise SpotCaptureError("invalid strict UTF-8 JSON body") from exc
        if type(parsed) is not dict:
            raise SpotCaptureError("raw transport body must be a JSON object")
        return body, parsed

    @staticmethod
    def _level_rows(rows: Any) -> None:
        if type(rows) is not list or len(rows) > 5000:
            raise SpotCaptureError("invalid or oversized book levels")
        seen = set()
        for row in rows:
            if type(row) is not list or len(row) != 2:
                raise SpotCaptureError("invalid book price level")
            try:
                if any(type(x) is not str or len(x) > 50 for x in row):
                    raise SpotCaptureError("book levels must use bounded decimal strings")
                p, qty = Decimal(row[0]), Decimal(row[1])
            except InvalidOperation as exc:
                raise SpotCaptureError("invalid book price/quantity") from exc
            if not (p.is_finite() and qty.is_finite()) or p <= 0 or qty < 0:
                raise SpotCaptureError("nonfinite/negative book price or quantity")
            if p in seen:
                raise SpotCaptureError("duplicated book price level")
            seen.add(p)

    def _classify(
        self, parsed: Mapping[str, Any], *, symbol: str, kind: str,
        received_at_ms: int,
    ) -> None:
        if kind == "snapshot":
            if (
                "lastUpdateId" not in parsed
                or type(parsed.get("lastUpdateId")) is not int
                or parsed["lastUpdateId"] <= 0
                or any(k in parsed for k in ("U", "u", "pu", "stream", "data"))
            ):
                raise SpotCaptureError("expected read-only REST Spot depth snapshot")
            self._level_rows(parsed.get("bids"))
            self._level_rows(parsed.get("asks"))
            if not parsed["bids"] or not parsed["asks"]:
                raise SpotCaptureError("empty REST depth snapshot")
            return
        expected = (
            f"{symbol.lower()}@aggTrade" if kind == "aggTrade"
            else f"{symbol.lower()}@depth@100ms"
        )
        # Accept either 100 ms or 1000 ms Spot diff stream variant.
        valid_streams = {expected}
        if kind == "depthUpdate":
            valid_streams.add(f"{symbol.lower()}@depth")
        if parsed.get("stream") not in valid_streams:
            raise SpotCaptureError("wrong combined-stream name or source symbol")
        frame = parsed.get("data")
        if type(frame) is not dict or frame.get("e") != kind or frame.get("s") != symbol:
            raise SpotCaptureError("wrong Spot stream event type or symbol")
        if kind == "aggTrade":
            try:
                OfflineMarketIntegrityJournal._validate(frame)
            except MarketEventError as exc:
                raise SpotCaptureError("malformed original aggregate trade") from exc
            timestamp = self._positive(frame["E"], "aggregate trade E")
            trade_time = self._positive(frame["T"], "aggregate trade T")
            if timestamp - trade_time > self.max_age_ms:
                raise SpotCaptureError("trade event/trade clock lag exceeds tolerance")
            earlier = min(timestamp, trade_time)
        else:
            first = self._positive(frame.get("U"), "U")
            last = self._positive(frame.get("u"), "u")
            timestamp = self._positive(frame.get("E"), "exchange depth event time")
            earlier = timestamp
            if first > last or "pu" in frame or "lastUpdateId" in frame:
                raise SpotCaptureError("wrong market or invalid Spot depth interval")
            self._level_rows(frame.get("b"))
            self._level_rows(frame.get("a"))
        if timestamp > received_at_ms + self.max_future_skew_ms:
            raise SpotCaptureError("source event is future-dated versus receipt")
        if received_at_ms - earlier > self.max_age_ms:
            raise SpotCaptureError("source event is stale versus receipt")

    def start_session(
        self, session_id: str, *, symbol: str, source: str,
        ws_endpoint: str, opened_at_ms: int,
    ) -> None:
        self._simulation_only()
        session_id = self._part(session_id, "session_id")
        symbol = self._symbol(symbol)
        source = self._part(source, "source")
        when = self._positive(opened_at_ms, "opened_at_ms")
        if ws_endpoint not in _WS_ORIGINS:
            raise SpotCaptureError("unrecognized Binance Spot combined WS endpoint")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("""
                    INSERT INTO offline_spot_capture_sessions
                    (session_id,symbol,source,ws_endpoint,opened_at_ms,last_receipt_ms)
                    VALUES (?,?,?,?,?,?)
                """, (session_id, symbol, source, ws_endpoint, when, when))
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def close_session(self, session_id: str, *, closed_at_ms: int) -> None:
        self._simulation_only()
        self._part(session_id, "session_id")
        when = self._positive(closed_at_ms, "closed_at_ms")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                session = self._session(conn, session_id)
                if session["closed_at_ms"] is not None or when < session["last_receipt_ms"]:
                    raise SpotCaptureError("session closed/reordered local clock")
                conn.execute("""
                    UPDATE offline_spot_capture_sessions SET closed_at_ms=?
                    WHERE session_id=?
                """, (when, session_id))
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    @staticmethod
    def _session(conn: sqlite3.Connection, session_id: str) -> sqlite3.Row:
        row = conn.execute("""
            SELECT * FROM offline_spot_capture_sessions WHERE session_id=?
        """, (session_id,)).fetchone()
        if row is None:
            raise SpotCaptureError("unknown capture session")
        return row

    def _record(
        self, session_id: str, *, raw: str | bytes, kind: str,
        endpoint: str, received_at_ms: int,
        request_started_at_ms: int | None,
        request_symbol: str | None,
    ) -> int:
        self._simulation_only()
        self._part(session_id, "session_id")
        received = self._positive(
            time.time_ns() // 1_000_000 if received_at_ms is None
            else received_at_ms, "received_at_ms",
        )
        if request_started_at_ms is not None:
            requested = self._positive(
                request_started_at_ms, "request_started_at_ms",
            )
            if requested > received:
                raise SpotCaptureError("REST request started after response receipt")
        else:
            requested = None
        body, parsed = self._parse(raw)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                session = self._session(conn, session_id)
                if session["closed_at_ms"] is not None:
                    raise SpotCaptureError("capture session is already closed")
                if received < session["last_receipt_ms"]:
                    raise SpotCaptureError("backdated/out-of-order local receipt clock")
                if requested is not None and requested < session["opened_at_ms"]:
                    raise SpotCaptureError("REST request predates websocket session")
                if kind == "snapshot" and endpoint not in _REST_DEPTH:
                    raise SpotCaptureError("unrecognized REST depth endpoint")
                if kind == "snapshot" and request_symbol != session["symbol"]:
                    raise SpotCaptureError("REST request symbol mismatch")
                if kind != "snapshot" and request_symbol is not None:
                    raise SpotCaptureError("websocket frame cannot carry REST request")
                if kind != "snapshot" and endpoint != session["ws_endpoint"]:
                    raise SpotCaptureError("websocket endpoint/session mismatch")
                self._classify(
                    parsed, symbol=session["symbol"], kind=kind,
                    received_at_ms=received,
                )
                count = conn.execute("""
                    SELECT COUNT(*) AS n FROM offline_spot_capture_records
                    WHERE session_id=?
                """, (session_id,)).fetchone()["n"]
                if count >= self.max_frames_per_session:
                    raise SpotCaptureError("capture capacity reached; no silent drops")
                digest = hashlib.sha256(body).hexdigest()
                cursor = conn.execute("""
                    INSERT INTO offline_spot_capture_records
                    (session_id,kind,received_at_ms,request_started_at_ms,
                     request_symbol,endpoint,raw_blob,sha256)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (session_id, kind, received, requested, request_symbol,
                      endpoint, body, digest))
                if self.forward_capture_audit:
                    conn.execute("""
                        INSERT INTO offline_forward_capture_intents
                        (capture_id,session_id,kind,received_at_ms,sha256)
                        VALUES (?,?,?,?,?)
                    """, (cursor.lastrowid, session_id, kind, received, digest))
                conn.execute("""
                    UPDATE offline_spot_capture_sessions SET last_receipt_ms=?
                    WHERE session_id=?
                """, (received, session_id))
                conn.execute("COMMIT")
                return cursor.lastrowid
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def record_ws(
        self, session_id: str, raw: str | bytes, *,
        stream_kind: str, received_at_ms: int | None = None,
    ) -> int:
        if stream_kind not in ("aggTrade", "depthUpdate"):
            raise SpotCaptureError("only Spot aggTrade/diff-depth permitted")
        with self._connect() as conn:
            session = self._session(conn, session_id)
            endpoint = session["ws_endpoint"]
        return self._record(
            session_id, raw=raw, kind=stream_kind, endpoint=endpoint,
            received_at_ms=received_at_ms, request_started_at_ms=None,
            request_symbol=None,
        )

    def record_snapshot(
        self, session_id: str, raw: str | bytes, *,
        rest_endpoint: str, request_symbol: str,
        request_started_at_ms: int, received_at_ms: int | None = None,
    ) -> int:
        self._symbol(request_symbol)
        with self._connect() as conn:
            session = self._session(conn, self._part(session_id, "session_id"))
            if request_symbol != session["symbol"]:
                raise SpotCaptureError("snapshot request symbol is not session instrument")
        return self._record(
            session_id, raw=raw, kind="snapshot", endpoint=rest_endpoint,
            received_at_ms=received_at_ms,
            request_started_at_ms=request_started_at_ms,
            request_symbol=request_symbol,
        )

    def replay_as_of(
        self, session_id: str, cutoff_ms: int, *, limit: int = 5000,
    ) -> list[CapturedSpotRecord]:
        """Verify every persisted byte and scope, then filter by FIRST receipt."""
        self._simulation_only()
        self._part(session_id, "session_id")
        cutoff = self._positive(cutoff_ms, "cutoff_ms")
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("invalid replay limit")
        with self._connect() as conn:
            session = self._session(conn, session_id)
            rows = conn.execute("""
                SELECT * FROM offline_spot_capture_records
                WHERE session_id=? AND received_at_ms <= ?
                ORDER BY capture_id LIMIT ?
            """, (session_id, cutoff, limit + 1)).fetchall()
        if len(rows) > limit:
            raise SpotCaptureError("replay limit exceeded; refusing truncated evidence")
        result = []
        prior_ms = session["opened_at_ms"]
        for row in rows:
            body = bytes(row["raw_blob"])
            if hashlib.sha256(body).hexdigest() != row["sha256"]:
                raise SpotCaptureError("raw body fingerprint mismatch")
            recovered, parsed = self._parse(body)
            if body != recovered:
                raise SpotCaptureError("stored raw bytes changed")
            if row["received_at_ms"] < prior_ms:
                raise SpotCaptureError("stored record receipt time regressed")
            if (
                session["closed_at_ms"] is not None
                and row["received_at_ms"] > session["closed_at_ms"]
            ):
                raise SpotCaptureError("record falls after session close")
            kind = row["kind"]
            endpoint = row["endpoint"]
            if kind == "snapshot":
                if (
                    endpoint not in _REST_DEPTH
                    or row["request_symbol"] != session["symbol"]
                    or type(row["request_started_at_ms"]) is not int
                    or not session["opened_at_ms"]
                    <= row["request_started_at_ms"] <= row["received_at_ms"]
                ):
                    raise SpotCaptureError("stored REST provenance invalid")
            elif kind not in ("aggTrade", "depthUpdate") or (
                endpoint != session["ws_endpoint"]
                or row["request_symbol"] is not None
                or row["request_started_at_ms"] is not None
            ):
                raise SpotCaptureError("stored WebSocket provenance invalid")
            self._classify(
                parsed, symbol=session["symbol"], kind=kind,
                received_at_ms=row["received_at_ms"],
            )
            prior_ms = row["received_at_ms"]
            result.append(CapturedSpotRecord(
                capture_id=row["capture_id"], session_id=session_id,
                symbol=session["symbol"], source=session["source"],
                kind=kind, received_at_ms=row["received_at_ms"],
                request_started_at_ms=row["request_started_at_ms"],
                endpoint=endpoint, raw=body,
                payload=parsed if kind == "snapshot" else parsed["data"],
            ))
        return result


    def verify_forward_capture_inventory(
        self, session_id: str, *, limit: int = 5000,
    ) -> list[CapturedSpotRecord]:
        """Audit every raw capture against same-COMMIT forward source intent.

        A matching intent only proves raw inclusion and immutable provenance;
        it does NOT imply the row was eligible for downstream publication.
        """
        self._simulation_only()
        self._part(session_id, "session_id")
        if not self.forward_capture_audit:
            raise SpotCaptureError("forward source intent audit was not enabled")
        if type(limit) is not int or not 1 <= limit <= 5000:
            raise SpotCaptureError("invalid bounded forward source audit limit")
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT r.capture_id,r.kind,r.received_at_ms,r.sha256,
                       a.capture_id AS intent_id,
                       a.session_id AS intent_session,
                       a.kind AS intent_kind,
                       a.received_at_ms AS intent_available,
                       a.sha256 AS intent_sha
                  FROM offline_spot_capture_records AS r
                  LEFT JOIN offline_forward_capture_intents AS a
                    ON a.capture_id=r.capture_id
                 WHERE r.session_id=?
                 ORDER BY r.capture_id LIMIT ?
            """, (session_id, limit + 1)).fetchall()
            extras = conn.execute("""
                SELECT COUNT(*) FROM offline_forward_capture_intents AS a
                LEFT JOIN offline_spot_capture_records AS r
                  ON r.capture_id=a.capture_id
                WHERE a.session_id=? AND
                   (r.capture_id IS NULL OR r.session_id<>a.session_id)
            """, (session_id,)).fetchone()[0]
        if len(rows) > limit or extras:
            raise SpotCaptureError("forward capture inventory exceeds limit or has orphan rows")
        verified = []
        for row in rows:
            if (
                row["intent_id"] != row["capture_id"]
                or row["intent_session"] != session_id
                or row["intent_kind"] != row["kind"]
                or row["intent_available"] != row["received_at_ms"]
                or row["intent_sha"] != row["sha256"]
            ):
                raise SpotCaptureError("atomic forward source intent missing or altered")
            verified.append(self.verify_captured_record(session_id, row["capture_id"]))
        return verified

    def verify_captured_record(
        self, session_id: str, capture_id: int,
    ) -> CapturedSpotRecord:
        """Bounded O(1)-row reread after raw SQLite COMMIT.

        This verifies the exact stored byte fingerprint/source scope and
        temporal receipt without re-reading thousands of earlier frames.
        A final whole-session replay must STILL validate sequence continuity,
        snapshot bridging, and history at close.
        """
        self._simulation_only()
        self._part(session_id, "session_id")
        capture_id = self._positive(capture_id, "capture_id")
        with self._connect() as conn:
            session = self._session(conn, session_id)
            row = conn.execute("""
                SELECT * FROM offline_spot_capture_records
                WHERE session_id=? AND capture_id=?
            """, (session_id, capture_id)).fetchone()
        if row is None:
            raise SpotCaptureError("missing committed source record")
        body = bytes(row["raw_blob"])
        if hashlib.sha256(body).hexdigest() != row["sha256"]:
            raise SpotCaptureError("source record fingerprint mismatch")
        recovered, parsed = self._parse(body)
        when = row["received_at_ms"]
        if (
            recovered != body
            or type(when) is not int
            or when < session["opened_at_ms"]
            or (
                session["closed_at_ms"] is not None
                and when > session["closed_at_ms"]
            )
        ):
            raise SpotCaptureError("source record bytes or time changed")
        kind = row["kind"]
        endpoint = row["endpoint"]
        if kind == "snapshot":
            if (
                endpoint not in _REST_DEPTH
                or row["request_symbol"] != session["symbol"]
                or type(row["request_started_at_ms"]) is not int
                or not session["opened_at_ms"]
                <= row["request_started_at_ms"] <= when
            ):
                raise SpotCaptureError("committed snapshot provenance invalid")
        elif kind not in ("aggTrade", "depthUpdate") or (
            endpoint != session["ws_endpoint"]
            or row["request_symbol"] is not None
            or row["request_started_at_ms"] is not None
        ):
            raise SpotCaptureError("committed WSS provenance invalid")
        self._classify(
            parsed, symbol=session["symbol"], kind=kind,
            received_at_ms=when,
        )
        return CapturedSpotRecord(
            capture_id=capture_id, session_id=session_id,
            symbol=session["symbol"], source=session["source"],
            kind=kind, received_at_ms=when,
            request_started_at_ms=row["request_started_at_ms"],
            endpoint=endpoint, raw=body,
            payload=parsed if kind == "snapshot" else parsed["data"],
        )
