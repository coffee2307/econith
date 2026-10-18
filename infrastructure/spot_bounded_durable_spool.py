"""Bounded source-first Spot capture and sealed research delivery.

The SQLite original + atomic source-intent table IS the durable spool.
Incoming WS frames never wait for receiver/derived/checkpoint I/O. Only
AFTER successful source seal and independent original replay are protected
research events delivered. Never claim this is continuous live ingress.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterable, Awaitable, Callable

from core.mode import QuantMode
from infrastructure.market_derived_research_receipts import OfflineDerivedResearchReceiptLedger
from infrastructure.market_named_research_subscribers import OfflineNamedResearchSubscriber
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_checkpoints import OfflineForwardSessionCheckpoints
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_incremental_shadow import IncrementalSpotShadowValidator
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter


@dataclass(frozen=True)
class SealedSpoolResearchReport:
    mode: str
    originals: int
    capture_queue_high_watermark: int
    reconstructed_intents: int
    independent_receiver_receipts: int
    derived_receipts: int
    named_receipts: int
    checkpoints: int
    source_session_closed: bool
    raw_source_bytes: int
    capture_elapsed_s: float
    replay_elapsed_s: float
    sealed_source_only: bool = True
    continuous_live_market: bool = False
    in_session_market_publication: bool = False
    history_continuity_verified: bool = False
    execution_started: bool = False
    orders_submitted: int = 0


async def capture_then_replay_sealed_spool(
    *, output_dir: Path, session_id: str,
    stream: AsyncIterable[str | bytes],
    snapshot_fetch: Callable[[str, int], Awaitable[str | bytes]],
    max_frames: int = 240, timeout_s: float = 120,
    queue_capacity: int = 128,
    max_book_staleness_ms: int = 2500,
    max_source_raw_bytes: int = 200_000_000,
) -> SealedSpoolResearchReport:
    """Offline/fixture-only deterministic proof before public adapter rollout.

    The capture callback enforces raw source sequence/book semantics only.
    It makes NO independent receiver or research-publisher side effects.
    Downstream work is performed only on an original that sealed successfully.
    Incomplete capture raises and preserves raw SQLite forensic evidence.
    """
    if (
        not isinstance(output_dir, Path) or not output_dir.is_absolute()
        or output_dir.exists() or output_dir != output_dir.resolve()
        or type(session_id) is not str or not session_id.startswith("rec_")
        or len(session_id) > 48
        or type(max_frames) is not int or not 10 <= max_frames <= 2000
        or type(queue_capacity) is not int or not 2 <= queue_capacity <= 128
        or type(timeout_s) not in (int, float) or not 5 <= timeout_s <= 120
        or type(max_book_staleness_ms) is not int
        or not 100 <= max_book_staleness_ms <= 30000
        or type(max_source_raw_bytes) is not int
        or not 1024 <= max_source_raw_bytes <= 200_000_000
        or not callable(snapshot_fetch) or stream is None
    ):
        raise ValueError("invalid bounded, new, offline spool scope")
    root = output_dir
    root.mkdir(parents=True, exist_ok=False)
    provider = lambda: QuantMode.SIMULATION
    journal = OfflineSpotCaptureJournal(
        root / "original.sqlite", mode_provider=provider,
        forward_capture_audit=True,
    )
    adapter = OptInSpotIngressAdapter(
        journal, symbol="BTCUSDT", queue_capacity=queue_capacity,
    )
    shadow = IncrementalSpotShadowValidator(
        journal, session_id=session_id, source="offline_fixture",
    )
    # Source-first: original+atomic intent COMMIT, bounded byte quota, and
    # strict as-of verification are the ONLY capture callback work.
    # A quota error leaves evidence quarantined and never starts replay.
    source_bytes = 0

    async def source_only(record):
        nonlocal source_bytes
        source_bytes += len(record.raw)
        if source_bytes > max_source_raw_bytes:
            raise RuntimeError("sealed original spool byte budget exceeded")
        await shadow.observe(record)

    capture_started = time.monotonic()
    capture = await adapter.capture_open_stream(
        stream, snapshot_fetch, session_id=session_id,
        source="offline_fixture", max_frames=max_frames,
        timeout_s=timeout_s, on_persisted=source_only,
    )
    capture_elapsed_s = time.monotonic() - capture_started
    source_proof = shadow.finish(capture)
    if source_proof.verified_original_records != (
        capture.websocket_frames + capture.snapshot_captures
    ):
        raise RuntimeError("sealing source inventory mismatch")

    # Reuse the already-reviewed, independently HMAC-receipted source
    # reconstruction and research delivery. Every ledger is separate from
    # immutable source. Recovery always marks session_certified=False.
    OfflineForwardSpotOutbox(
        root / "forward_outbox.sqlite",
        session_id=session_id, mode_provider=provider,
    )
    OfflineDerivedResearchReceiptLedger(
        root / "derived.sqlite", mode_provider=provider,
    )
    for name in ("research_audit", "research_monitor"):
        OfflineNamedResearchSubscriber(
            root / (name + ".sqlite"), name=name,
            expected_source="verified_forward_spot_shadow",
            mode_provider=provider,
        )
    OfflineForwardSessionCheckpoints(
        root / "forward_checkpoints.sqlite",
        session_id=session_id, mode_provider=provider,
    )
    replay_started = time.monotonic()
    delivered = await recover_staged_forward_spot(
        root=root, session_id=session_id,
        max_envelopes=2000, reconstruct_missing=True,
        max_book_staleness_ms=max_book_staleness_ms,
        persistent_receiver=True,
    )
    replay_elapsed_s = time.monotonic() - replay_started
    if (
        not delivered.source_session_closed
        or not delivered.original_eligibility_rescan_verified
        or delivered.staged_envelopes <= 0
        or delivered.staged_envelopes != delivered.reconstructed_missing_intents
        or delivered.staged_envelopes != delivered.receiver_receipts
        or delivered.staged_envelopes != delivered.outbox_derived_acked
        or delivered.staged_envelopes != delivered.forward_checkpoints
        or delivered.primary_derived_receipts <= 0
        or delivered.independent_named_receipts != 2 * delivered.primary_derived_receipts
        or delivered.session_certified
        or delivered.source_coverage_certified
    ):
        raise RuntimeError("sealed source/receiver/derived/checkpoint mismatch")
    return SealedSpoolResearchReport(
        mode="SIMULATION",
        originals=source_proof.verified_original_records,
        capture_queue_high_watermark=capture.queue_high_watermark,
        reconstructed_intents=delivered.reconstructed_missing_intents,
        independent_receiver_receipts=delivered.receiver_receipts,
        derived_receipts=delivered.primary_derived_receipts,
        named_receipts=delivered.independent_named_receipts,
        checkpoints=delivered.forward_checkpoints,
        source_session_closed=True,
        raw_source_bytes=source_bytes,
        capture_elapsed_s=round(capture_elapsed_s, 3),
        replay_elapsed_s=round(replay_elapsed_s, 3),
    )
