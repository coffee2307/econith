"""Fail-closed research replay from persisted original Spot transport frames.

The adapter never reads live sockets, calls REST, publishes to EventBus or
creates orders. It validates source/receipt evidence from the capture journal
and constructs an as-of eligible depth book only after Spot snapshot bridging.
"""
from __future__ import annotations

from dataclasses import dataclass

from infrastructure.spot_ingress_capture import (
    CapturedSpotRecord, OfflineSpotCaptureJournal, SpotCaptureError,
)
from infrastructure.spot_depth_sync import DepthBookView, OfflineSpotDepthSynchronizer


@dataclass(frozen=True)
class SpotCaptureReplay:
    session_id: str
    symbol: str
    source: str
    as_of_ms: int
    total_records: int
    trades: tuple[CapturedSpotRecord, ...]
    last_agg_trade_id: int | None
    depth_status: str
    book: DepthBookView | None


class OfflineSpotCaptureReplayer:
    """Replay immutable capture bytes using only their recorded first receipt.

    Aggregate-trade continuity starts at the first observed ID, never claims
    completeness before that baseline. A failed diff interval poisons the
    order-book replay; it MUST NOT be turned into an apparently healthy book.
    """

    def __init__(self, capture: OfflineSpotCaptureJournal) -> None:
        if not isinstance(capture, OfflineSpotCaptureJournal):
            raise ValueError("raw capture journal required")
        self.capture = capture

    def replay(
        self, session_id: str, *, as_of_ms: int, limit: int = 5000,
    ) -> SpotCaptureReplay:
        records = self.capture.replay_as_of(session_id, as_of_ms, limit=limit)
        if not records:
            raise SpotCaptureError("no captured transport records at requested as-of")
        first = records[0]
        depth = OfflineSpotDepthSynchronizer(
            symbol=first.symbol, market="binance_spot",
            source=first.source, session_id=session_id,
            mode_provider=self.capture._mode_provider,
        )
        trades = []
        last_agg_id: int | None = None
        last_trade_payload: dict | None = None
        for record in records:
            if (
                record.session_id != session_id
                or record.symbol != first.symbol
                or record.source != first.source
            ):
                raise SpotCaptureError("source/session identity changed during replay")
            if record.kind == "aggTrade":
                sequence = record.payload["a"]
                if last_agg_id is not None:
                    if sequence == last_agg_id:
                        if record.payload != last_trade_payload:
                            raise SpotCaptureError("conflicting duplicate aggregate-trade ID")
                        continue
                    if sequence != last_agg_id + 1:
                        raise SpotCaptureError("aggregate-trade sequence gap or rollback")
                trades.append(record)
                last_agg_id, last_trade_payload = sequence, record.payload
            elif record.kind == "depthUpdate":
                result = depth.ingest_diff(
                    record.payload, received_at_ms=record.received_at_ms,
                )
                if result.status == "RESET_REQUIRED":
                    raise SpotCaptureError(
                        "depth replay lost continuity; new session/snapshot required"
                    )
            elif record.kind == "snapshot":
                result = depth.load_snapshot(
                    record.payload, received_at_ms=record.received_at_ms,
                    session_id=session_id,
                )
                if result.status == "RESET_REQUIRED":
                    raise SpotCaptureError("depth replay snapshot/diff continuity failed")
            else:
                raise SpotCaptureError("unsupported captured record type")
        book = depth.view(as_of_ms=as_of_ms) if depth.status == "SYNCED" else None
        return SpotCaptureReplay(
            session_id, first.symbol, first.source, as_of_ms,
            len(records), tuple(trades), last_agg_id, depth.status, book,
        )
