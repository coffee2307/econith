"""Isolated, explicitly opted-in PUBLIC Spot forward research ASGI preview.

No default routing. Startup validates a public Binance clock preflight before
starting one bounded no-account/no-order source task. This is read-only
SIMULATION market research, not 24/7 service or execution authority. A task
failure stays FAILED; it NEVER triggers legacy-source fallback.
"""
from __future__ import annotations

import asyncio
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_forward_verified_research import (
    ForwardSpotResearchStatus, observe_forward_spot_research,
)


@dataclass(frozen=True)
class VerifiedForwardPreviewStatus:
    mode: str
    market_data_policy: str
    status: str
    session_id: str
    orders_submitted: int
    execution_started: bool
    legacy_streamer_started: bool
    continuous_live_market: bool
    history_continuity_verified: bool
    result: dict | None
    failure_class: str | None


class VerifiedForwardPreviewRuntime:
    def __init__(self, session_id: str, task: asyncio.Task):
        self.session_id = session_id
        self._task = task

    @property
    def status(self) -> VerifiedForwardPreviewStatus:
        task = self._task
        failure = None
        result = None
        label = "RUNNING"
        if task.done():
            if task.cancelled():
                label = "CANCELLED"
            else:
                err = task.exception()
                if err is not None:
                    label = "FAILED"
                    failure = type(err).__name__
                else:
                    verified = task.result()
                    if not isinstance(verified, ForwardSpotResearchStatus) or (
                        not verified.session_complete
                        or verified.execution_started
                        or verified.orders_submitted != 0
                        or verified.continuous_live_market
                        or verified.history_continuity_verified
                    ):
                        label = "FAILED"
                        failure = "UnverifiedPreviewResult"
                    else:
                        label = "COMPLETE_BOUNDED_RESEARCH"
                        result = asdict(verified)
        return VerifiedForwardPreviewStatus(
            mode="SIMULATION",
            market_data_policy="verified_forward_preview",
            status=label, session_id=self.session_id,
            orders_submitted=0, execution_started=False,
            legacy_streamer_started=False,
            continuous_live_market=False, history_continuity_verified=False,
            result=result, failure_class=failure,
        )

    async def close(self) -> None:
        if not self._task.done():
            self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


async def start_verified_forward_preview(
    *, output_dir: Path,
    max_frames: int = 160,
    timeout_s: float = 45,
    max_book_staleness_ms: int = 2500,
) -> VerifiedForwardPreviewRuntime:
    if (
        not isinstance(output_dir, Path)
        or not output_dir.is_absolute()
        or output_dir.exists()
        or output_dir != output_dir.resolve()
        or type(max_frames) is not int or not 10 <= max_frames <= 2000
        or type(timeout_s) not in (int,float) or not 5 <= timeout_s <= 120
        or type(max_book_staleness_ms) is not int
        or not 100 <= max_book_staleness_ms <= 30000
    ):
        raise ValueError("unsafe bounded forward preview output/configuration")
    # Fail closed BEFORE starting any WS or creating an evidence directory.
    await measure_public_clock(max_abs_offset_ms=3000,max_rtt_ms=2000)
    session_id = "rec_forward_" + secrets.token_hex(12)
    task = asyncio.create_task(
        observe_forward_spot_research(
            output_dir=output_dir, session_id=session_id,
            source="binance_public_simulation", allow_public_network=True,
            max_frames=max_frames, timeout_s=timeout_s,
            max_book_staleness_ms=max_book_staleness_ms,
        ),
        name="research-only-public-spot-preview",
    )
    return VerifiedForwardPreviewRuntime(session_id,task)
