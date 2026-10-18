"""Opt-in within-session forward Spot research, strictly read-only.

A raw Binance Spot original is committed to the source SQLite first. A
post-COMMIT shadow re-reads/validates original bytes, local availability and
trade/depth sequence. ONLY then may the resulting *as-of* trade or synchronized
book view be sent to an independent HMAC-authenticated local receiver process
and admitted to the protected SIMULATION EventBus research graph.

This is an experimental in-session read-only shadow, NOT an institutional
continuous verified default feed, complete history, order authority or durable
arbitrary EventBus subscriber. A late failure quarantines the ENTIRE source
session even if earlier read-only projections were observed. There is no
cross-DB atomicity between source capture, receiver and EventBus.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, AsyncIterable, Awaitable, Callable

from ci.phase1_authenticated_receiver_ipc import (
    accept_batch_via_authenticated_subprocess,
)
from core.event_bus import Event, EventBus, RawMarketAdmissionDenied
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope,
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.market_derived_research_receipts import (
    OfflineDerivedResearchReceiptLedger,
)
from infrastructure.market_named_research_subscribers import (
    OfflineNamedResearchSubscriber,
)
from infrastructure.preprocessing.pipeline import MarketDataPipeline
from infrastructure.spot_incremental_shadow import IncrementalSpotShadowValidator
from infrastructure.spot_ingress_capture import CapturedSpotRecord, OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportResult,
)

_TOPIC_COUNTS = ("md.ticker", "indicator.volume_delta", "indicator.obi")
_SOURCE = "verified_forward_spot_shadow"


class ForwardSpotResearchError(RuntimeError):
    """Forward-as-of evidence or independent receiver ACK was not proven."""


@dataclass(frozen=True)
class ForwardSpotResearchStatus:
    mode: str
    session_id: str
    original_records: int
    verified_trades: int
    forwarded_trades: int
    forwarded_books: int
    pre_sync_trades_not_forwarded: int
    receiver_receipts: int
    primary_derived_receipts: int
    named_derived_receipts: int
    book_final_update_id: int
    all_receipts_independent: bool
    research_only: bool
    publication_in_session: bool
    session_complete: bool
    history_continuity_verified: bool
    continuous_live_market: bool
    execution_started: bool
    orders_submitted: int


async def observe_forward_spot_research(
    *, output_dir: Path, session_id: str,
    stream: AsyncIterable[str | bytes] | None = None,
    snapshot_fetch: Callable[[str, int], Awaitable[str | bytes]] | None = None,
    allow_public_network: bool = False, source: str = "offline_fixture",
    max_frames: int = 160, timeout_s: float = 45,
    queue_capacity: int = 128,
    max_book_staleness_ms: int = 2500,
) -> ForwardSpotResearchStatus:
    """One real *in-session* research pass (no default startup/auto retry).

    Injected test streams do NOT attest a TLS peer. For live public endpoints,
    pass allow_public_network=True and no injected stream/snapshot; a trusted
    operator must separately check local public clock before invoking this
    module, as the bounded capture adapter does not certify local NTP.
    """
    if (
        not isinstance(output_dir, Path) or not output_dir.is_absolute()
        or output_dir.exists()
        or type(session_id) is not str or not session_id.startswith("rec_")
        or len(session_id) > 48
        or type(max_frames) is not int or not 10 <= max_frames <= 2000
        or type(timeout_s) not in (int, float) or not 5 <= timeout_s <= 120
        or type(queue_capacity) is not int or not 2 <= queue_capacity <= 8192
        or type(max_book_staleness_ms) is not int
        or not 100 <= max_book_staleness_ms <= 30000
    ):
        raise ValueError("invalid fresh, bounded forward research configuration")
    offline = stream is not None and snapshot_fetch is not None
    if (
        (stream is None) != (snapshot_fetch is None)
        or (offline and allow_public_network)
        or (not offline and allow_public_network is not True)
        or (offline and source != "offline_fixture")
        or (not offline and source != "binance_public_simulation")
    ):
        raise PermissionError("explicit and exclusive offline/real-public source mode required")
    root = output_dir.resolve()
    if root != output_dir or root.parent.is_symlink():
        raise ValueError("forward research evidence root must be absolute and unaliased")
    root.mkdir(parents=True, exist_ok=False)
    provider = lambda: QuantMode.SIMULATION
    journal = OfflineSpotCaptureJournal(root / "original.sqlite", mode_provider=provider)
    adapter = OptInSpotIngressAdapter(
        journal, symbol="BTCUSDT", queue_capacity=queue_capacity,
    )
    shadow = IncrementalSpotShadowValidator(
        journal, session_id=session_id, source=source,
    )
    bus = EventBus(mode_provider=provider)
    publisher = bus.activate_verified_market_only(max_inflight=1)
    derived_publisher = bus.activate_verified_derived_only()
    pipeline = MarketDataPipeline(bus, verified_derived_publisher=derived_publisher)
    pipeline.register()
    derived_db = OfflineDerivedResearchReceiptLedger(
        root / "derived.sqlite", mode_provider=provider,
    )
    named = [
        OfflineNamedResearchSubscriber(
            root / (name + ".sqlite"), name=name, expected_source=_SOURCE,
            mode_provider=provider,
        ) for name in ("research_audit", "research_monitor")
    ]
    for consumer in named:
        consumer.register(bus)
    receiver_db = root / "independent_receiver.sqlite"
    observed = dict.fromkeys(_TOPIC_COUNTS, 0)
    forwarded_trades = forwarded_books = pre_sync = 0
    last_book_id: int | None = None
    admitted = 0
    async def derived_observer(event: Event) -> None:
        if event.payload.get("source") != _SOURCE:
            raise ForwardSpotResearchError("derived event has untrusted forward provenance")
        derived_db.accept(
            topic=event.topic,
            session_id=event.payload["verified_session_id"],
            ordinal=event.payload["verified_source_ordinal"],
            source_record_id=event.payload["verified_source_record_id"],
            source_available_at_ms=event.payload["verified_source_available_at_ms"],
            projection=event.payload,
        )
        observed[event.topic] += 1

    for topic in observed:
        bus.subscribe(topic, derived_observer)

    async def forward(record: CapturedSpotRecord) -> None:
        nonlocal forwarded_trades, forwarded_books, pre_sync, last_book_id, admitted
        before = shadow.trades
        # Source COMMIT and independent disk re-read BEFORE any receiver IPC
        # or EventBus publish. On any error the capture adapter ends the session.
        await shadow.observe(record)
        now = record.received_at_ms
        if record.kind == "aggTrade":
            if shadow.trades == before:
                return  # identical source repeat, no duplicate signal
            if shadow.book.status != "SYNCED":
                pre_sync += 1
                return
            if (
                shadow.book.available_at_ms is None
                or now - shadow.book.available_at_ms > max_book_staleness_ms
            ):
                raise ForwardSpotResearchError(
                    "stale synchronized book: no forward market admission"
                )
            topic = "md.aggTrade"
            ordinal = forwarded_trades + 1
            view = None
            source_record = f"{session_id}:aggTrade:{record.capture_id}"
            raw = record.payload
        elif record.kind in ("snapshot", "depthUpdate"):
            if shadow.book.status != "SYNCED":
                return  # never transform a raw diff into an alleged full book
            view = shadow.book.view(as_of_ms=now)
            if view.last_update_id == last_book_id:
                return
            topic = "md.depth"
            ordinal = forwarded_books + 1
            source_record = f"{session_id}:book:{record.capture_id}"
            raw = {
                "lastUpdateId":view.last_update_id,
                "bids":[list(x) for x in view.bids[:20]],
                "asks":[list(x) for x in view.asks[:20]],
            }
        else:
            raise ForwardSpotResearchError("unsupported source evidence kind")
        if view is not None and view.available_at_ms > now:
            raise ForwardSpotResearchError("forward book cannot use future availability")
        projection = {
            "session_id":session_id,
            "source_capture_id":record.capture_id,
            "source_record_id":source_record,
            "topic":topic,
            "source_available_at_ms":now,
            "source_raw_sha256":hashlib.sha256(record.raw).hexdigest(),
            "verified_asof_book_id":view.last_update_id if view is not None else None,
            "full_verified_frame":raw,
        }
        body, digest = OfflineMarketProjectionCursor._serialize(projection)
        envelope = ProjectionEnvelope(
            source_consumer="forward_spot",
            receiver="read_only_market",
            ingest_id=record.capture_id,
            event_id=f"md:forward:{session_id}:{record.capture_id}",
            available_at_ms=now,
            projection_json=body,
            projection_fingerprint=digest,
        )
        # An actual fresh OS child owns the independent receiver SQLite.
        # Signing verifies the EXACT ordered envelope/receipt identity.
        confirmations = await asyncio.to_thread(
            accept_batch_via_authenticated_subprocess,
            [envelope], receiver_db=receiver_db,
            now_ms=max(time.time_ns() // 1_000_000, now),
        )
        if len(confirmations) != 1 or confirmations[0].receipt_id != (
            OfflineIndependentProjectionReceiver._receipt_id(envelope)
        ):
            raise ForwardSpotResearchError("independent authenticated receiver ACK missing")
        # ONLY the approved read-only pipeline/observers run on this bus.
        # Subscription completion alone is never a durable external ACK.
        await publisher.publish_raw_confirmed(
            topic, frame=raw,
            **({"symbol":"BTCUSDT"} if topic == "md.depth" else {}),
            source=_SOURCE, historical_replay=False, forward_research=True,
            available_at_ms=now,
            verified_session_id=session_id,
            verified_source_record_id=source_record,
            verified_source_ordinal=ordinal,
            verified_source_available_at_ms=now,
            receiver_receipt_id=confirmations[0].receipt_id,
        )
        await bus.wait_verified_idle(timeout_s=15)
        admitted += 1
        if topic == "md.aggTrade":
            forwarded_trades += 1
        else:
            forwarded_books += 1
            last_book_id = view.last_update_id

    await bus.start()
    try:
        if offline:
            result = await adapter.capture_open_stream(
                stream, snapshot_fetch, session_id=session_id, source=source,
                max_frames=max_frames, timeout_s=timeout_s,
                on_persisted=forward,
            )
        else:
            result = await adapter.capture_public_once(
                allow_network=True, session_id=session_id,
                max_frames=max_frames, timeout_s=timeout_s,
                on_persisted=forward,
            )
        proof = shadow.finish(result)
        wanted = {
            "md.ticker": forwarded_trades,
            "indicator.volume_delta": forwarded_trades,
            "indicator.obi": forwarded_books,
        }
        if forwarded_trades <= 0 or forwarded_books <= 0 or observed != wanted:
            raise ForwardSpotResearchError("no accepted source-bound forward derived graph")
        await bus.wait_verified_idle(timeout_s=15)
        totals = {topic:derived_db.verified_count(session_id, topic) for topic in wanted}
        if totals != wanted:
            raise ForwardSpotResearchError("primary research observer receipt mismatch")
        independent = [consumer.verify(session_id, wanted) for consumer in named]
        receiver = OfflineIndependentProjectionReceiver(receiver_db, mode_provider=provider)
        receipts = receiver.receipts("forward_spot", "read_only_market")
        if (
            len(receipts) != admitted
            or len({x.ingest_id for x in receipts}) != admitted
            or result.websocket_frames + result.snapshot_captures != proof.verified_original_records
        ):
            raise ForwardSpotResearchError("source/receiver forward admission mismatch")
        # A forged legacy raw/derived producer is still barred from this bus.
        for topic in ("md.aggTrade", "md.ticker"):
            try:
                await bus.publish(topic, source=_SOURCE)
            except RawMarketAdmissionDenied:
                pass
            else:
                raise ForwardSpotResearchError("protected forward EventBus bypass")
        return ForwardSpotResearchStatus(
            mode="SIMULATION", session_id=session_id,
            original_records=proof.verified_original_records,
            verified_trades=proof.verified_agg_trades,
            forwarded_trades=forwarded_trades, forwarded_books=forwarded_books,
            pre_sync_trades_not_forwarded=pre_sync,
            receiver_receipts=len(receipts),
            primary_derived_receipts=sum(totals.values()),
            named_derived_receipts=sum(x.durable_receipts for x in independent),
            book_final_update_id=proof.book_last_update_id,
            all_receipts_independent=True, research_only=True,
            publication_in_session=True, session_complete=True,
            history_continuity_verified=False, continuous_live_market=False,
            execution_started=False, orders_submitted=0,
        )
    finally:
        await bus.stop()
