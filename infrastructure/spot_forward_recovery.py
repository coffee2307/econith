"""Quarantined replay of staged forward research envelopes only.

There is intentionally NO source socket, clock API, execution bridge, or new
market event discovery. A verified original source can contain more rows than
the producer outbox: a crash between source commit and stage is NOT silently
repaired. Recovery only redelivers and reconciles persisted immutable intents
in original ordinal order, and the resulting report always says
source_coverage_certified=False, session_certified=False.

Separate receiver DB + existing three derived observer ledgers are required.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from ci.phase1_authenticated_receiver_ipc import accept_batch_via_authenticated_subprocess
from core.event_bus import Event, EventBus
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import OfflineIndependentProjectionReceiver
from infrastructure.market_derived_research_receipts import OfflineDerivedResearchReceiptLedger
from infrastructure.market_named_research_subscribers import OfflineNamedResearchSubscriber
from infrastructure.preprocessing.pipeline import MarketDataPipeline
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox, ForwardOutboxError
from infrastructure.spot_forward_checkpoints import OfflineForwardSessionCheckpoints
from infrastructure.spot_forward_eligibility import rescan_forward_eligibility
from infrastructure.spot_forward_owner import exclusive_forward_owner
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal

_SOURCE = "verified_forward_spot_shadow"
_TOPICS = ("md.ticker", "indicator.volume_delta", "indicator.obi")


@dataclass(frozen=True)
class ForwardStagedRecoveryReport:
    session_id: str
    staged_envelopes: int
    receiver_receipts: int
    primary_derived_receipts: int
    independent_named_receipts: int
    outbox_derived_acked: int
    source_session_closed: bool
    reconstructed_missing_intents: int = 0
    original_eligibility_rescan_verified: bool = False
    source_coverage_certified: bool = False
    session_certified: bool = False
    execution_started: bool = False
    orders_submitted: int = 0
    forward_checkpoints: int = 0
    checkpoint_suffix_repaired: int = 0


async def recover_staged_forward_spot(
    *, root: Path, session_id: str,
    max_envelopes: int = 2000,
    reconstruct_missing: bool = False,
    max_book_staleness_ms: int = 2500,
) -> ForwardStagedRecoveryReport:
    """Operator-invoked proof for staged rows; never resumes raw capture."""
    if (
        not isinstance(root, Path) or not root.is_absolute()
        or not root.is_dir() or root != root.resolve()
        or type(max_envelopes) is not int or not 1 <= max_envelopes <= 2000
        or type(reconstruct_missing) is not bool
        or type(max_book_staleness_ms) is not int
        or not 100 <= max_book_staleness_ms <= 30000
    ):
        raise ForwardOutboxError("unsafe bounded forward recovery directory")
    with exclusive_forward_owner(root, role="STAGED_RECOVERY"):
        source = root / "original.sqlite"
        ledger_path = root / "forward_outbox.sqlite"
        receiver_path = root / "independent_receiver.sqlite"
        primary_path = root / "derived.sqlite"
        names = ("research_audit", "research_monitor")
        needed = [source, ledger_path, primary_path] + [
            root / (name + ".sqlite") for name in names
        ]
        if any(not p.is_file() or p.is_symlink() or p.resolve() != p
               for p in needed):
            raise ForwardOutboxError("all previously owned durable evidence ledgers required")
        if receiver_path.exists() and (
            not receiver_path.is_file()
            or receiver_path.is_symlink()
            or receiver_path.resolve() != receiver_path
        ):
            raise ForwardOutboxError("invalid existing receiver evidence path")
        provider = lambda: QuantMode.SIMULATION
        journal = OfflineSpotCaptureJournal(
            source, mode_provider=provider, forward_capture_audit=True,
        )
        inventory = journal.verify_forward_capture_inventory(session_id)
        if len(inventory) > max_envelopes + 500:
            raise ForwardOutboxError("raw source inventory exceeds recovery budget")
        outbox = OfflineForwardSpotOutbox(
            ledger_path, session_id=session_id, mode_provider=provider,
        )
        with journal._connect() as db:
            session = db.execute(
                "SELECT closed_at_ms,source FROM offline_spot_capture_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if session is None:
            raise ForwardOutboxError("original captured session missing")
        rebuilt = 0
        if reconstruct_missing:
            # Validate the entire original prefix and every pre-existing
            # immutable envelope BEFORE adding any missing intents.
            scan = await rescan_forward_eligibility(
                journal, session_id=session_id, source=session["source"],
                max_originals=min(5000, max_envelopes + 500),
                max_book_staleness_ms=max_book_staleness_ms,
            )
            candidates = {e.ingest_id:e for e in scan.envelopes}
            if len(candidates) > max_envelopes:
                raise ForwardOutboxError("reconstructed intent capacity exceeded")
            existing = outbox.items()
            if any(
                old.envelope.ingest_id not in candidates
                or old.envelope != candidates[old.envelope.ingest_id]
                for old in existing
            ):
                raise ForwardOutboxError(
                    "staged intent differs from independently reconstructed as-of source"
                )
            already = {row.envelope.ingest_id for row in existing}
            for envelope in scan.envelopes:
                if envelope.ingest_id not in already:
                    outbox.stage(envelope)
                    rebuilt += 1
        items = outbox.verify_source(journal, source_session_id=session_id)
        if len(items) > max_envelopes:
            raise ForwardOutboxError("staged recovery exceeds bounded resource limit")
        if not receiver_path.is_file() and not (
            reconstruct_missing
            and rebuilt == len(items)
            and all(x.status == "STAGED" for x in items)
        ):
            # Never silently replace an independently owned receiver ledger
            # once any prior producer intent/ACK might have been delivered.
            raise ForwardOutboxError("missing independent receiver evidence")
        named = [
            OfflineNamedResearchSubscriber(
                root / (name + ".sqlite"), name=name,
                expected_source=_SOURCE, mode_provider=provider,
            ) for name in names
        ]
        primary = OfflineDerivedResearchReceiptLedger(
            primary_path, mode_provider=provider,
        )
        receiver = OfflineIndependentProjectionReceiver(
            receiver_path, mode_provider=provider,
        )
        bus = EventBus(mode_provider=provider)
        pub = bus.activate_verified_market_only(max_inflight=1)
        derived = bus.activate_verified_derived_only()
        MarketDataPipeline(bus, verified_derived_publisher=derived).register()
        wanted = {"md.ticker":0,"indicator.volume_delta":0,"indicator.obi":0}

        async def primary_observer(event: Event) -> None:
            primary.accept(
                topic=event.topic,
                session_id=event.payload["verified_session_id"],
                ordinal=event.payload["verified_source_ordinal"],
                source_record_id=event.payload["verified_source_record_id"],
                source_available_at_ms=event.payload["verified_source_available_at_ms"],
                projection=event.payload,
            )
        for topic in _TOPICS:
            bus.subscribe(topic, primary_observer)
        for consumer in named:
            consumer.register(bus)

        await bus.start()
        try:
            for row in items:
                e = row.envelope
                data = json.loads(e.projection_json)
                original = journal.verify_captured_record(session_id, e.ingest_id)
                if (
                    data["source_available_at_ms"] != original.received_at_ms
                    or data["source_capture_id"] != e.ingest_id
                    or data["topic"] not in ("md.aggTrade","md.depth")
                ):
                    raise ForwardOutboxError("original data diverges from staged envelope")
                # Re-send EXACT original wire intent; signed receiver-owned receipt
                # is idempotent even after a crash between its COMMIT and our ACK.
                confirmations = await asyncio.to_thread(
                    accept_batch_via_authenticated_subprocess,
                    [e], receiver_db=receiver_path,
                    now_ms=max(__import__("time").time_ns() // 1_000_000, e.available_at_ms),
                )
                if len(confirmations) != 1:
                    raise ForwardOutboxError("independent receiver ACK unavailable")
                outbox.ack_receiver(e, confirmations[0])
                topic = data["topic"]
                if topic == "md.aggTrade":
                    wanted["md.ticker"] += 1
                    wanted["indicator.volume_delta"] += 1
                    ordinal = wanted["md.ticker"]
                else:
                    wanted["indicator.obi"] += 1
                    ordinal = wanted["indicator.obi"]
                await pub.publish_raw_confirmed(
                    topic, frame=data["full_verified_frame"],
                    **({"symbol":"BTCUSDT"} if topic == "md.depth" else {}),
                    source=_SOURCE, historical_replay=False, forward_research=True,
                    available_at_ms=e.available_at_ms,
                    verified_session_id=session_id,
                    verified_source_record_id=data["source_record_id"],
                    verified_source_ordinal=ordinal,
                    verified_source_available_at_ms=e.available_at_ms,
                    receiver_receipt_id=confirmations[0].receipt_id,
                )
                await bus.wait_verified_idle(timeout_s=20)
                outbox.ack_derived(e)
            actual = {topic:primary.verified_count(session_id, topic) for topic in _TOPICS}
            if actual != wanted:
                raise ForwardOutboxError("primary derived receipt count not replayed")
            named_proofs = [consumer.verify(session_id, wanted) for consumer in named]
            received = receiver.receipts("forward_spot", "read_only_market")
            if (
                len(received) != len(items)
                or len(outbox.items()) != len(items)
                or any(r.status != "DERIVED_ACKED" for r in outbox.items())
            ):
                raise ForwardOutboxError("incomplete staged forward recovery")
            # Old durable checkpoints must be an EXACT prefix of fully
            # re-ACKed source outbox, or recovery is forbidden. A crash after
            # derived COMMIT may leave a missing checkpoint *suffix* only.
            checkpoint_count = suffix_repaired = 0
            checkpoint_file = root/"forward_checkpoints.sqlite"
            if checkpoint_file.is_file():
                checkpoints=OfflineForwardSessionCheckpoints(
                    checkpoint_file,session_id=session_id,mode_provider=provider,
                )
                completed=outbox.items()
                preceding=checkpoints.verified_prefix(completed)
                repaired=checkpoints.reconcile(completed)
                checkpoint_count=repaired.checkpointed_events
                suffix_repaired=checkpoint_count-preceding.checkpointed_events
            return ForwardStagedRecoveryReport(
                session_id=session_id,
                staged_envelopes=len(items),
                receiver_receipts=len(received),
                primary_derived_receipts=sum(actual.values()),
                independent_named_receipts=sum(x.durable_receipts for x in named_proofs),
                outbox_derived_acked=len(items),
                source_session_closed=session["closed_at_ms"] is not None,
                reconstructed_missing_intents=rebuilt,
                original_eligibility_rescan_verified=reconstruct_missing,
                forward_checkpoints=checkpoint_count,
                checkpoint_suffix_repaired=suffix_repaired,
            )
        finally:
            await bus.stop()
