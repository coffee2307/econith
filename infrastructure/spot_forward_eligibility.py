"""Deterministic per-original, per-receipt as-of Spot forward eligibility scan.

Read-only from the existing atomic raw-capture inventory; never reads a
WebSocket, calls REST, rewrites original bytes or sends orders. A source may
be incomplete due to a crash. Only the committed original prefix is scanned,
and missing exchange observations cannot be inferred or fabricated.

The scanner duplicates admission *criteria*, not downstream side effects, so
a change that conflicts with already-staged immutable outbox envelopes fails
closed rather than rewriting prior research history.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import ProjectionEnvelope
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.spot_incremental_shadow import IncrementalSpotShadowValidator
from infrastructure.spot_ingress_capture import CapturedSpotRecord, OfflineSpotCaptureJournal

_SOURCE_CONSUMER = "forward_spot"
_RECEIVER = "read_only_market"


class ForwardEligibilityError(ValueError):
    """Original source cannot be safely reconstructed at captured as-of."""


@dataclass(frozen=True)
class ForwardEligibilityScan:
    envelopes: tuple[ProjectionEnvelope, ...]
    raw_originals: int
    trades_before_book: int
    forwarded_trades: int
    forwarded_books: int
    last_book_id: int | None


async def rescan_forward_eligibility(
    journal: OfflineSpotCaptureJournal, *, session_id: str,
    source: str, max_originals: int = 2500,
    max_book_staleness_ms: int = 2500,
) -> ForwardEligibilityScan:
    if (
        type(max_originals) is not int or not 1 <= max_originals <= 5000
        or type(max_book_staleness_ms) is not int
        or not 100 <= max_book_staleness_ms <= 30000
        or source not in ("offline_fixture", "binance_public_simulation")
    ):
        raise ForwardEligibilityError("invalid forward reconstruction scope")
    originals = journal.verify_forward_capture_inventory(
        session_id, limit=max_originals,
    )
    if not originals:
        raise ForwardEligibilityError("empty or unavailable forward original inventory")
    shadow = IncrementalSpotShadowValidator(
        journal, session_id=session_id, source=source,
    )
    envelopes = []
    trades = books = early = 0
    last_book_id: int | None = None
    for raw_record in originals:
        prior = shadow.trades
        await shadow.observe(raw_record)
        now = raw_record.received_at_ms
        if raw_record.kind == "aggTrade":
            if shadow.trades == prior:
                continue  # identical source trade ID
            if shadow.book.status != "SYNCED":
                early += 1
                continue
            if (
                shadow.book.available_at_ms is None
                or now - shadow.book.available_at_ms > max_book_staleness_ms
            ):
                raise ForwardEligibilityError(
                    "stale synchronized book: quarantined committed prefix"
                )
            topic = "md.aggTrade"
            source_id = f"{session_id}:aggTrade:{raw_record.capture_id}"
            raw_frame = raw_record.payload
            book_id = None
            trades += 1
        elif raw_record.kind in ("snapshot", "depthUpdate"):
            if shadow.book.status != "SYNCED":
                continue
            book = shadow.book.view(as_of_ms=now)
            if book.last_update_id == last_book_id:
                continue
            topic = "md.depth"
            source_id = f"{session_id}:book:{raw_record.capture_id}"
            raw_frame = {
                "lastUpdateId":book.last_update_id,
                "bids":[list(level) for level in book.bids[:20]],
                "asks":[list(level) for level in book.asks[:20]],
            }
            book_id = book.last_update_id
            last_book_id = book_id
            books += 1
        else:
            raise ForwardEligibilityError("unexpected immutable source type")
        projection = {
            "session_id":session_id,
            "source_capture_id":raw_record.capture_id,
            "source_record_id":source_id,
            "topic":topic,
            "source_available_at_ms":now,
            "source_raw_sha256":hashlib.sha256(raw_record.raw).hexdigest(),
            "verified_asof_book_id":book_id,
            "full_verified_frame":raw_frame,
        }
        body, digest = OfflineMarketProjectionCursor._serialize(projection)
        envelopes.append(ProjectionEnvelope(
            source_consumer=_SOURCE_CONSUMER,
            receiver=_RECEIVER,
            ingest_id=raw_record.capture_id,
            event_id=f"md:forward:{session_id}:{raw_record.capture_id}",
            available_at_ms=now,
            projection_json=body,
            projection_fingerprint=digest,
        ))
    return ForwardEligibilityScan(
        envelopes=tuple(envelopes),
        raw_originals=len(originals),
        trades_before_book=early,
        forwarded_trades=trades,
        forwarded_books=books,
        last_book_id=last_book_id,
    )
