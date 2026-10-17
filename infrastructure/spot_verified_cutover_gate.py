"""Fail-closed preflight preventing a false verified-only ingress cutover.

The current application still wires BinanceWebSocketStreamer to EventBus
topics md.aggTrade / md.depth. That streamer uses partial depth20 snapshots
and may overlay SIMULATION values. An operator setting "verified_only" must
get an explicit startup failure until *all* subscribers have a verified-only
source and the legacy producer is disconnected.
"""
from __future__ import annotations

import os

_SUPPORTED = frozenset(("legacy", "verified_only"))


class VerifiedIngressNotReady(RuntimeError):
    """Default runtime cannot claim admission via the offline research journal."""


def enforce_verified_only_spot_ingress(
    policy: str | None = None,
) -> str:
    """Check a fail-closed startup contract without opening sockets or DBs.

    Default 'legacy' retains today's behavior and makes NO quality assurance
    claim.  'verified_only' is deliberately NOT enabled just because the
    research-only capture modules exist; there is no runtime wiring yet.
    """
    chosen = (
        os.environ.get("ECONITH_SPOT_INGRESS_POLICY", "legacy")
        if policy is None else policy
    )
    if not isinstance(chosen, str) or chosen not in _SUPPORTED:
        raise VerifiedIngressNotReady(
            "invalid ECONITH_SPOT_INGRESS_POLICY; allowed: legacy, verified_only"
        )
    if chosen == "verified_only":
        raise VerifiedIngressNotReady(
            "verified-only Spot ingress requested but default application still "
            "registers BinanceWebSocketStreamer and unverified EventBus raw "
            "subscribers; startup stopped instead of silently bypassing "
            "original-byte verification and durable receiver ACK"
        )
    return chosen
