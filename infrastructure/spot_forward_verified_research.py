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
import os
from contextlib import nullcontext
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
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_checkpoints import OfflineForwardSessionCheckpoints
from infrastructure.spot_forward_owner import exclusive_forward_owner
from infrastructure.spot_forward_stage_profile import BoundedStageProfiler
from infrastructure.spot_projection_durable_cursor import OfflineSpotProjectionCursor
from infrastructure.market_persistent_hmac_receiver import PersistentHmacResearchReceiver
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
    OptInSpotIngressAdapter, SpotTransportResult, _PUBLIC_WS, _PUBLIC_REST,
)
from infrastructure.spot_public_pinned_lease import PinnedPublicSpotLease

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
    queue_high_watermark: int = 0
    receiver_process_starts: int = 0
    receiver_transport: str = "one_shot_hmac"
    max_receiver_ack_latency_ms: float = 0.0
    forward_checkpoints: int = 0
    forward_checkpoint_sha256: str | None = None
    spool_original_cursor: int = 0
    max_projector_lag_ms: int = 0
    max_spool_pending_originals: int = 0


async def observe_forward_spot_research(
    *, output_dir: Path, session_id: str,
    stream: AsyncIterable[str | bytes] | None = None,
    snapshot_fetch: Callable[[str, int], Awaitable[str | bytes]] | None = None,
    allow_public_network: bool = False, source: str = "offline_fixture",
    max_frames: int = 160, timeout_s: float = 45,
    queue_capacity: int = 128,
    max_book_staleness_ms: int = 2500,
    persistent_receiver: bool = False,
    public_ws_idle_timeout_s: float = 30.0,
    public_lease: PinnedPublicSpotLease | None = None,
    spooled_in_session: bool = False,
    allow_experimental_public_spool: bool = False,
    max_projector_lag_ms: int = 5000,
    max_pending_originals: int = 512,
    max_source_raw_bytes: int = 200_000_000,
    experiment_reuse_derived_connections: bool = False,
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
        or type(persistent_receiver) is not bool
        or type(public_ws_idle_timeout_s) not in (int,float)
        or not 0.1 <= public_ws_idle_timeout_s <= 120
        or (public_lease is not None and type(public_lease) is not PinnedPublicSpotLease)
        or type(spooled_in_session) is not bool
        or type(allow_experimental_public_spool) is not bool
        or type(max_projector_lag_ms) is not int
        or not 100 <= max_projector_lag_ms <= 30000
        or type(max_pending_originals) is not int
        or not 1 <= max_pending_originals <= 1000
        or type(experiment_reuse_derived_connections) is not bool
        or type(max_source_raw_bytes) is not int
        or not 1024 <= max_source_raw_bytes <= 200_000_000
    ):
        raise ValueError("invalid fresh, bounded forward research configuration")
    offline = stream is not None and snapshot_fetch is not None
    if (
        (stream is None) != (snapshot_fetch is None)
        or (offline and allow_public_network)
        or (not offline and allow_public_network is not True)
        or (offline and source != "offline_fixture")
        or (not offline and source != "binance_public_simulation")
        or (public_lease is not None and (offline or not allow_public_network))
    ):
        raise PermissionError("explicit and exclusive offline/real-public source mode required")
    if experiment_reuse_derived_connections and (
        not spooled_in_session or
        (not offline and not allow_experimental_public_spool)
    ):
        raise PermissionError("derived connection reuse requires explicitly bounded research spool")
    if spooled_in_session and (
        queue_capacity != 128 or not persistent_receiver
    ):
        raise ValueError("live spool requires bounded 128 ingress and signed persistent receiver")
    if allow_experimental_public_spool and (
        not spooled_in_session or offline or not allow_public_network
        or max_frames > 1000 or max_projector_lag_ms > 5000
        or max_pending_originals > 512
    ):
        raise PermissionError("experimental public spool trial requires finite safe profile")
    if spooled_in_session and not offline and not allow_experimental_public_spool:
        raise PermissionError("unreviewed public continuous spool is disabled")
    root = output_dir.resolve()
    if root != output_dir or root.parent.is_symlink():
        raise ValueError("forward research evidence root must be absolute and unaliased")
    root.mkdir(parents=True, exist_ok=False)
    with exclusive_forward_owner(root, role="CAPTURE"):
        # Explicit research-only diagnostic. No venue/source admission changes.
        profiler = (BoundedStageProfiler(root / "stage_latency.json")
                    if os.environ.get("ECONITH_PHASE1_STAGE_PROFILE") == "1" else None)
        trace = profiler.measure if profiler else lambda _stage: nullcontext()
        completed = False
        provider = lambda: QuantMode.SIMULATION
        journal = OfflineSpotCaptureJournal(
            root / "original.sqlite", mode_provider=provider, forward_capture_audit=True,
        )
        adapter = OptInSpotIngressAdapter(
            journal, symbol="BTCUSDT", queue_capacity=queue_capacity,
            stage_profile=profiler,
        )
        shadow = IncrementalSpotShadowValidator(
            journal, session_id=session_id, source=source,
        )
        ingress_shadow = (
            IncrementalSpotShadowValidator(
                journal, session_id=session_id, source=source,
            ) if spooled_in_session else None
        )
        spool_cursor = (
            OfflineSpotProjectionCursor(
                root / "projection_cursor.sqlite",
                journal=journal, session_id=session_id,
            ) if spooled_in_session else None
        )
        bus = EventBus(mode_provider=provider)
        publisher = bus.activate_verified_market_only(max_inflight=1)
        derived_publisher = bus.activate_verified_derived_only()
        pipeline = MarketDataPipeline(bus, verified_derived_publisher=derived_publisher)
        pipeline.register()
        derived_db = OfflineDerivedResearchReceiptLedger(
            root / "derived.sqlite", mode_provider=provider,
            reuse_connection=experiment_reuse_derived_connections,
        )
        named = [
            OfflineNamedResearchSubscriber(
                root / (name + ".sqlite"), name=name, expected_source=_SOURCE,
                mode_provider=provider,
                reuse_connection=experiment_reuse_derived_connections,
            ) for name in ("research_audit", "research_monitor")
        ]
        for consumer in named:
            consumer.register(bus)
        receiver_db = root / "independent_receiver.sqlite"
        outbox = OfflineForwardSpotOutbox(
            root / "forward_outbox.sqlite", session_id=session_id,
            mode_provider=provider,
        )
        checkpoints = OfflineForwardSessionCheckpoints(
            root / "forward_checkpoints.sqlite",session_id=session_id,
            mode_provider=provider,
        )
        observed = dict.fromkeys(_TOPIC_COUNTS, 0)
        forwarded_trades = forwarded_books = pre_sync = 0
        last_book_id: int | None = None
        admitted = 0
        receiver_ack_max_ms = 0.0
        peak_pending_originals = 0
        highest_lag_ms = 0
        published_before_source_close = False
        source_spool_bytes = 0
        receiver_client = (PersistentHmacResearchReceiver(receiver_db)
                           if persistent_receiver else None)
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
            nonlocal forwarded_trades, forwarded_books, pre_sync, last_book_id, admitted, receiver_ack_max_ms, highest_lag_ms, published_before_source_close
            def enforce_live_age() -> None:
                nonlocal highest_lag_ms
                if spooled_in_session:
                    lag_ms = (time.time_ns() // 1_000_000) - record.received_at_ms
                    if lag_ms < -1000 or lag_ms > max_projector_lag_ms:
                        raise ForwardSpotResearchError("projector source delivery lag exceeded")
                    highest_lag_ms = max(highest_lag_ms, lag_ms)
            enforce_live_age()
            before = shadow.trades
            # Source COMMIT and independent disk re-read BEFORE any receiver IPC
            # or EventBus publish. On any error the capture adapter ends the session.
            with trace("shadow_verify"):
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
            # Stage immutable source-bound intent durably BEFORE IPC. If the
            # child COMMITs then this process dies, exact recovery can retry it.
            with trace("outbox_stage"):
                outbox.stage(envelope)
            # Both modes require an independent SQLite COMMIT and HMAC-bound
            # receipt before publishing. Persistent mode reuses one child
            # across the finite session, never across source identities.
            ipc_start = time.monotonic()
            with trace("receiver_hmac_ack"):
                if receiver_client is None:
                    confirmations = await asyncio.to_thread(
                        accept_batch_via_authenticated_subprocess,
                        [envelope], receiver_db=receiver_db,
                        now_ms=max(time.time_ns() // 1_000_000, now),
                    )
                else:
                    confirmations = await receiver_client.accept_batch(
                        [envelope], now_ms=max(time.time_ns() // 1_000_000, now),
                    )
            receiver_ack_max_ms = max(
                receiver_ack_max_ms, (time.monotonic()-ipc_start)*1000,
            )
            if len(confirmations) != 1 or confirmations[0].receipt_id != (
                OfflineIndependentProjectionReceiver._receipt_id(envelope)
            ):
                raise ForwardSpotResearchError("independent authenticated receiver ACK missing")
            with trace("outbox_receiver_ack"):
                outbox.ack_receiver(envelope, confirmations[0])
            # Signed receiver ACK may take time: recheck freshness just
            # BEFORE any protected EventBus publication, not only at dequeue.
            enforce_live_age()
            # ONLY the approved read-only pipeline/observers run on this bus.
            # Subscription completion alone is never a durable external ACK.
            with trace("eventbus_publish"):
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
            if spooled_in_session and not capture_closed:
                published_before_source_close = True
            with trace("derived_subscriber_wait"):
                await bus.wait_verified_idle(timeout_s=15)
            # Handler completion is not itself durable. Advance after BOTH
            # independent receiver and derived-ledger counts are visible.
            with trace("outbox_derived_ack"):
                outbox.ack_derived(envelope)
            # The source session is STILL OPEN: durable independently tied
            # producer/receiver/derived ACK proof is checkpointed per event.
            with trace("checkpoint_commit"):
                checkpoints.append_live(
                    envelope,
                    completed_item=outbox.completed_item(envelope),
                    expected_ordinal=admitted+1,
                )
            admitted += 1
            if topic == "md.aggTrade":
                forwarded_trades += 1
            else:
                forwarded_books += 1
                last_book_id = view.last_update_id

        signal = asyncio.Event()
        latest_original = 0
        advanced_original = 0
        capture_closed = False
        projector_task: asyncio.Task | None = None

        async def capture_spool_input(record: CapturedSpotRecord) -> None:
            nonlocal latest_original, peak_pending_originals, source_spool_bytes
            assert ingress_shadow is not None
            source_spool_bytes += len(record.raw)
            if source_spool_bytes > max_source_raw_bytes:
                raise ForwardSpotResearchError("durable original spool byte capacity exceeded")
            await ingress_shadow.observe(record)
            if record.capture_id != latest_original + 1:
                raise ForwardSpotResearchError("source spool original ordinal gap")
            latest_original = record.capture_id
            backlog = latest_original - advanced_original
            peak_pending_originals = max(peak_pending_originals, backlog)
            if backlog > max_pending_originals:
                raise ForwardSpotResearchError("source spool pending capacity exceeded")
            signal.set()
            if projector_task is not None and projector_task.done():
                projector_task.result()

        async def project_committed_originals() -> None:
            nonlocal advanced_original
            assert spool_cursor is not None
            start = spool_cursor.read_verified()
            if start.last_capture_id != 0:
                raise ForwardSpotResearchError("live spool refuses resumed source generation")
            while True:
                await signal.wait()
                signal.clear()
                while advanced_original < latest_original:
                    capture_id = advanced_original + 1
                    raw_record = journal.verify_captured_record(session_id, capture_id)
                    await forward(raw_record)
                    state = spool_cursor.advance(raw_record)
                    advanced_original = state.last_capture_id
                if capture_closed:
                    return

        await bus.start()
        try:
            if receiver_client is not None:
                await receiver_client.start()
            if spooled_in_session:
                projector_task = asyncio.create_task(
                    project_committed_originals(), name="spot-durable-spool-projector",
                )
            callback = capture_spool_input if spooled_in_session else forward
            if offline:
                result = await adapter.capture_open_stream(
                    stream, snapshot_fetch, session_id=session_id, source=source,
                    max_frames=max_frames, timeout_s=timeout_s,
                    on_persisted=callback,
                )
            elif public_lease is not None:
                # A verified-public lease is established only by the pinned
                # TLS connector, never by an arbitrary injected fixture.
                public_lease.ensure_open()
                public_lease.segments_started += 1
                result = await adapter.capture_open_stream(
                    public_lease.stream, public_lease.snapshot,
                    session_id=session_id, source="binance_public_simulation",
                    ws_endpoint=_PUBLIC_WS, rest_endpoint=_PUBLIC_REST,
                    max_frames=max_frames, timeout_s=timeout_s,
                    on_persisted=callback,
                    idle_timeout_s=public_ws_idle_timeout_s,
                    require_frame_limit=True,
                )
            else:
                result = await adapter.capture_public_once(
                    allow_network=True, session_id=session_id,
                    max_frames=max_frames, timeout_s=timeout_s,
                    on_persisted=callback,
                    idle_timeout_s=public_ws_idle_timeout_s,
                )
            if spooled_in_session:
                capture_closed = True
                signal.set()
                assert projector_task is not None and ingress_shadow is not None
                await asyncio.wait_for(projector_task, timeout=timeout_s)
                ingress_proof = ingress_shadow.finish(result)
                if ingress_proof.verified_original_records != latest_original:
                    raise ForwardSpotResearchError("ingress spool source seal mismatch")
                if spool_cursor is None or spool_cursor.read_verified().last_capture_id != latest_original:
                    raise ForwardSpotResearchError("durable spool original cursor incomplete")
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
            inventory = journal.verify_forward_capture_inventory(session_id)
            if len(inventory) != proof.verified_original_records:
                raise ForwardSpotResearchError("raw source/atomic capture-intent coverage mismatch")
            saved = outbox.verify_source(journal, source_session_id=session_id)
            if len(saved) != admitted or any(x.status != "DERIVED_ACKED" for x in saved):
                raise ForwardSpotResearchError("producer outbox/consumer ACK mismatch")
            checkpoint_proof = checkpoints.verified_prefix(saved)
            if checkpoint_proof.checkpointed_events != admitted:
                raise ForwardSpotResearchError("forward live checkpoint/ACK count mismatch")
            # A forged legacy raw/derived producer is still barred from this bus.
            for topic in ("md.aggTrade", "md.ticker"):
                try:
                    await bus.publish(topic, source=_SOURCE)
                except RawMarketAdmissionDenied:
                    pass
                else:
                    raise ForwardSpotResearchError("protected forward EventBus bypass")
            completed = True
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
                publication_in_session=(published_before_source_close
                                        if spooled_in_session else True),
                session_complete=True,
                history_continuity_verified=False, continuous_live_market=False,
                execution_started=False, orders_submitted=0,
                queue_high_watermark=result.queue_high_watermark,
                receiver_process_starts=(receiver_client.process_starts
                                         if receiver_client is not None else admitted),
                receiver_transport=("persistent_hmac" if receiver_client is not None
                                    else "one_shot_hmac"),
                max_receiver_ack_latency_ms=receiver_ack_max_ms,
                forward_checkpoints=checkpoint_proof.checkpointed_events,
                forward_checkpoint_sha256=checkpoint_proof.tail_sha256,
                spool_original_cursor=advanced_original,
                max_projector_lag_ms=highest_lag_ms,
                max_spool_pending_originals=peak_pending_originals,
            )
        finally:
            if projector_task is not None and not projector_task.done():
                projector_task.cancel()
                await asyncio.gather(projector_task, return_exceptions=True)
            try:
                if receiver_client is not None:
                    await receiver_client.close()
                await bus.stop()
            finally:
                try:
                    for consumer in named:
                        consumer.close()
                    derived_db.close()
                finally:
                    if profiler is not None:
                        # Diagnostic output is not source/ACK evidence.
                        profiler.write(completed=completed)
