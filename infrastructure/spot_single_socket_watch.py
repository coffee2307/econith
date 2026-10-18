"""Read-only supervised forward research across segments on ONE pinned public WSS.

Successive bounded segments reuse a single hostname-verified TLS socket.
Every segment has a fresh original SQLite capture, REST snapshot, independent
receiver/derived ledgers, and an in-session hash checkpoint. On any failed
segment, discard the socket and reconnect the NEXT segment, always with a
fresh book and an UNKNOWN history gap. Do not silently claim losslessness:
even two segments on the SAME WSS have an unverified market boundary.

In contrast to the earlier supervisor, this does NOT open a socket per
successful 160-frame segment; only a failed lease / first attempt connects.
No live orders, CCXT, default runtime cutover, or private API credentials.
"""
from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from infrastructure.spot_forward_supervisor import (
    ForwardSupervisorStatus,run_forward_research_supervisor,
)
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from infrastructure.spot_public_pinned_lease import (
    PinnedPublicSpotLease,open_pinned_public_spot_lease,
)


@dataclass(frozen=True)
class SingleSocketWatchStatus:
    supervisor: ForwardSupervisorStatus
    public_socket_starts: int
    successful_socket_reuses: int
    discarded_broken_sockets: int
    public_tls_lease_established_by_trusted_connector: bool
    no_unobserved_market_gaps_certified: bool = False
    execution_started: bool = False
    orders_submitted: int = 0


async def run_single_socket_forward_watch(
    *, output_dir: Path,
    allow_public_market_data: bool = False,
    attempts: int = 3,max_frames: int = 160,
    timeout_s: float = 45, public_ws_idle_timeout_s: float = 30,
    max_book_staleness_ms: int = 2500,
    max_total_storage_bytes: int = 100_000_000,
    watch_duration_s: float | None = None,
    fresh_socket_per_segment: bool = False,
    clock_check: Callable[[],Awaitable[bool]] | None = None,
    _lease_factory=None,
    _monotonic_s=None,
) -> SingleSocketWatchStatus:
    if allow_public_market_data is not True:
        raise PermissionError("public Binance research requires opt-in")
    if clock_check is None or not callable(clock_check):
        raise PermissionError("public clock preflight required before each segment")
    if type(fresh_socket_per_segment) is not bool:
        raise ValueError("invalid public lease freshness policy")
    # Input policy, owner lock and budgets are independently validated again
    # by run_forward_research_supervisor BEFORE any source connection.
    factory=_lease_factory or open_pinned_public_spot_lease
    stack:AsyncExitStack | None=None
    lease:PinnedPublicSpotLease | None=None
    starts=reuses=discarded=0

    async def close_lease(*, failed: bool) -> None:
        nonlocal stack,lease,discarded
        if lease is not None:
            lease.invalidate()
        if stack is not None:
            try:
                await stack.aclose()
            finally:
                stack=None
                lease=None
                if failed:
                    discarded+=1

    async def checked_clock() -> bool:
        # A prior WSS continues buffering venue messages when no segment
        # reader is attached. A failed network/time preflight can therefore
        # leave a "healthy" but stale socket behind. Never reuse it.
        try:
            result=await clock_check()
        except BaseException as exc:
            await close_lease(failed=True)
            # httpx.ConnectTimeout is typically the Binance public time
            # preflight. Persist an unambiguous STATIC failure code, not a
            # potentially private HTTP exception message or URL.
            if type(exc).__name__=="ConnectTimeout" and (
                type(exc).__module__.startswith("httpx")
                or type(exc).__module__.startswith("httpcore")
            ):
                from infrastructure.spot_public_capture_adapter import SpotTransportError
                raise SpotTransportError(
                    "public clock preflight connection timed out",
                    reason="CLOCK_PREFLIGHT_CONNECT_TIMEOUT",
                ) from exc
            raise
        if result is not True:
            await close_lease(failed=True)
        return result

    async def consume(directory:Path,sid:str,iteration:int):
        nonlocal stack,lease,starts,reuses,discarded
        reused = lease is not None
        if lease is None:
            stack=AsyncExitStack()
            try:
                lease=await stack.enter_async_context(
                    factory(allow_public_market_data=True),
                )
            except BaseException:
                await stack.aclose()
                stack=None
                raise
            if type(lease) is not PinnedPublicSpotLease:
                await stack.aclose()
                stack=None
                lease=None
                raise RuntimeError("untrusted public Spot lease provider")
            lease.ensure_open()
            starts+=1
        try:
            result=await observe_forward_spot_research(
                output_dir=directory,session_id=sid,
                source="binance_public_simulation",
                allow_public_network=True,
                public_lease=lease,
                max_frames=max_frames,timeout_s=timeout_s,
                max_book_staleness_ms=max_book_staleness_ms,
                persistent_receiver=True,
                public_ws_idle_timeout_s=public_ws_idle_timeout_s,
            )
            if reused:
                reuses+=1
            if fresh_socket_per_segment:
                # Test only bounded independent captures on NEW public WSS
                # sockets. No assumed continuity across discarded segments.
                await close_lease(failed=False)
            return result
        except BaseException:
            # A failed or suspect source must never reach the next segment.
            await close_lease(failed=True)
            raise

    try:
        supervisor=await run_forward_research_supervisor(
            output_dir=output_dir,
            allow_public_network=True,
            attempts=attempts,max_frames=max_frames,
            timeout_s=timeout_s,max_total_storage_bytes=max_total_storage_bytes,
            max_book_staleness_ms=max_book_staleness_ms,
            persistent_receiver=True,
            public_ws_idle_timeout_s=public_ws_idle_timeout_s,
            watch_duration_s=watch_duration_s,
            clock_check=checked_clock,
            capture_factory=consume,
            _monotonic_s=_monotonic_s,
        )
    finally:
        await close_lease(failed=False)
    return SingleSocketWatchStatus(
        supervisor=supervisor,
        public_socket_starts=starts,
        successful_socket_reuses=reuses,
        discarded_broken_sockets=discarded,
        public_tls_lease_established_by_trusted_connector=_lease_factory is None,
    )
