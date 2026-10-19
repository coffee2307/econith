"""Incremental, post-COMMIT Binance Spot source validation (research shadow).

Attached to the single-writer capture adapter's optional on_persisted hook:
each original is durably committed AND reread/verified before it enters this
small synchronous validator. It checks aggregate trade sequence and the
live Spot snapshot/diff book incrementally, not only at session close.

This is a SHADOW VALIDATOR. It MUST NOT publish to EventBus or authorize
orders, position sizing, PnL/backtests or market execution. The existing
independent receiver ACK/full closed-session replay gate remains mandatory
before historical research events may be released to subscribers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.mode import QuantMode
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_depth_sync import OfflineSpotDepthSynchronizer
from infrastructure.spot_ingress_capture import (
    CapturedSpotRecord, OfflineSpotCaptureJournal,
)
from infrastructure.spot_public_capture_adapter import SpotTransportResult


class IncrementalSpotIntegrityError(RuntimeError):
    """No further source samples from this session may be trusted."""


@dataclass(frozen=True)
class IncrementalSpotIntegrityStatus:
    session_id: str
    verified_original_records: int
    verified_agg_trades: int
    verified_depth_updates: int
    verified_rest_snapshots: int
    book_status: str
    book_last_update_id: int | None
    last_local_receipt_ms: int | None
    first_trade_baseline_only: bool
    complete_exchange_history: bool
    publisher_events: int
    orders_submitted: int


class IncrementalSpotShadowValidator:
    """Reject a broken public feed immediately after source SQLite COMMIT.

    Journal.lookup is O(1) indexed; the shadow keeps at most one trade
    frame and the bounded depth synchronizer. No unverified event reaches
    strategy or indicator subscribers from this class.
    """

    def __init__(
        self, journal: OfflineSpotCaptureJournal, *,
        session_id: str, symbol: str = "BTCUSDT",
        source: str = "binance_public_simulation",
    ) -> None:
        if not isinstance(journal, OfflineSpotCaptureJournal):
            raise TypeError("original Spot capture journal required")
        journal._simulation_only()
        journal._part(session_id, "session_id")
        journal._part(source, "source")
        journal._symbol(symbol)
        self.journal = journal
        self.session_id = session_id
        self.symbol = symbol
        self.source = source
        self.book = OfflineSpotDepthSynchronizer(
            symbol=symbol, source=source, session_id=session_id,
            mode_provider=lambda: QuantMode.SIMULATION,
        )
        self._failed = False
        self._last_capture_id: int | None = None
        self._last_received_at: int | None = None
        self._last_trade_id: int | None = None
        self._last_trade_payload: dict[str, Any] | None = None
        self.originals = 0
        self.ws_frames = 0
        self.trades = 0
        self.depth_updates = 0
        self.snapshots = 0

    async def observe(self, record: CapturedSpotRecord) -> None:
        """Called strictly after the *exact* original SQLite row commits."""
        if self._failed:
            raise IncrementalSpotIntegrityError(
                "incremental source already invalid; reconnect in NEW session"
            )
        try:
            if (
                not isinstance(record, CapturedSpotRecord)
                or record.session_id != self.session_id
                or record.symbol != self.symbol
                or record.source != self.source
            ):
                raise IncrementalSpotIntegrityError("wrong raw capture session or symbol")
            # Independently load the indexed original from SQLite, not from
            # mutable callback labels. This is safe while the session is open.
            persisted = self.journal.verify_captured_record(
                self.session_id, record.capture_id,
            )
            if persisted != record:
                raise IncrementalSpotIntegrityError(
                    "incremental source callback differs from committed bytes"
                )
            if (
                (self._last_capture_id is not None
                 and record.capture_id <= self._last_capture_id)
                or (self._last_received_at is not None
                    and record.received_at_ms < self._last_received_at)
            ):
                raise IncrementalSpotIntegrityError(
                    "source capture or local receipt order regressed"
                )
            if record.kind == "aggTrade":
                self.ws_frames += 1
                agg = record.payload["a"]
                if self._last_trade_id is not None:
                    if agg == self._last_trade_id:
                        if record.payload != self._last_trade_payload:
                            raise IncrementalSpotIntegrityError(
                                "conflicting duplicate aggregate trade"
                            )
                    elif agg != self._last_trade_id + 1:
                        raise IncrementalSpotIntegrityError(
                            "aggregate trade sequence gap; new session required"
                        )
                if self._last_trade_id != agg:
                    self.trades += 1
                    self._last_trade_id = agg
                    self._last_trade_payload = record.payload
            elif record.kind == "depthUpdate":
                self.ws_frames += 1
                self.depth_updates += 1
                result = self.book.ingest_diff(
                    record.payload, received_at_ms=record.received_at_ms,
                )
                if result.status == "RESET_REQUIRED":
                    raise IncrementalSpotIntegrityError(
                        "depth update gap/invalid book: new REST snapshot required"
                    )
            elif record.kind == "snapshot":
                self.snapshots += 1
                if self.snapshots != 1:
                    raise IncrementalSpotIntegrityError(
                        "more than one REST snapshot in a single reviewed session"
                    )
                result = self.book.load_snapshot(
                    record.payload, received_at_ms=record.received_at_ms,
                    session_id=self.session_id,
                )
                if result.status in ("RESET_REQUIRED", "AWAITING_NEW_SNAPSHOT"):
                    raise IncrementalSpotIntegrityError(
                        "snapshot cannot bridge captured depth intervals"
                    )
            else:
                raise IncrementalSpotIntegrityError("unsupported captured source kind")
            self.originals += 1
            self._last_capture_id = record.capture_id
            self._last_received_at = record.received_at_ms
        except Exception:
            # Once even one required raw record fails, this session cannot
            # regain trust; operator must reconnect with fresh snapshot.
            self._failed = True
            raise

    def finish(self, result: SpotTransportResult) -> IncrementalSpotIntegrityStatus:
        """Fail closed if the incremental book and sealed replay diverge."""
        if self._failed or not isinstance(result, SpotTransportResult):
            raise IncrementalSpotIntegrityError("incremental session failed")
        if (
            result.session_id != self.session_id
            or result.symbol != self.symbol
            or self.snapshots != 1
            or self.trades <= 0
            or result.websocket_frames != self.ws_frames
        ):
            raise IncrementalSpotIntegrityError(
                "incremental capture counts/session do not match source"
            )
        if self.book.status != "SYNCED":
            raise IncrementalSpotIntegrityError(
                "incremental book not snapshot/diff synchronized"
            )
        book = self.book.view(as_of_ms=result.last_received_at_ms)
        if book.last_update_id != result.last_update_id:
            raise IncrementalSpotIntegrityError(
                "online shadow and capture-replay book IDs differ"
            )
        # The original session has now been closed by the transport.
        # Independent offline recheck catches poisoned earlier row bytes.
        replay = OfflineSpotCaptureReplayer(self.journal).replay(
            self.session_id, as_of_ms=result.last_received_at_ms,
            limit=5000,
        )
        if (
            replay.book is None
            or replay.book.last_update_id != book.last_update_id
            or replay.total_records != self.originals
            or len(replay.trades) != self.trades
        ):
            raise IncrementalSpotIntegrityError(
                "incremental shadow and sealed source replay diverged"
            )
        return self.status()

    def status(self) -> IncrementalSpotIntegrityStatus:
        return IncrementalSpotIntegrityStatus(
            session_id=self.session_id,
            verified_original_records=self.originals,
            verified_agg_trades=self.trades,
            verified_depth_updates=self.depth_updates,
            verified_rest_snapshots=self.snapshots,
            book_status=self.book.status,
            book_last_update_id=self.book.last_update_id,
            last_local_receipt_ms=self._last_received_at,
            first_trade_baseline_only=True,
            complete_exchange_history=False,
            publisher_events=0,
            orders_submitted=0,
        )
