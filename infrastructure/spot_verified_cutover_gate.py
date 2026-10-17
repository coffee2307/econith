"""Fail-closed preflight preventing a false verified-only ingress cutover.

The default legacy application still wires BinanceWebSocketStreamer and
unverified partial depth20 topics. The explicitly configured "verified_only"
runtime is instead a completely isolated, read-only *historical* research
replay of a previously closed verified Spot capture session; it never
starts the legacy streamer, default engine, CCXT or network ingestion.
Without all research inputs, the opt-in fails before app startup.
"""
from __future__ import annotations

import os
from pathlib import Path

_SUPPORTED = frozenset(("legacy", "verified_only"))


class VerifiedIngressNotReady(RuntimeError):
    """Default runtime cannot claim admission via the offline research journal."""


def enforce_verified_only_spot_ingress(
    policy: str | None = None,
) -> str:
    """Check a fail-closed startup contract without opening sockets or DBs.

    Default 'legacy' retains today's behavior and makes NO quality assurance
    claim. 'verified_only' permits ONLY the configured isolated historical
    research session; never a continuous verified live feed.
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
        # Only the physically isolated, historical SIMULATION research graph
        # may opt into the verified-only *main process*. No live streamer,
        # CCXT, default execution engine, or ordinary HTTP mutation routes
        # are started. A plain flag without complete evidence is denied.
        source = os.environ.get("ECONITH_SPOT_VERIFIED_REPLAY_DB", "")
        session = os.environ.get("ECONITH_SPOT_VERIFIED_REPLAY_SESSION", "")
        output = os.environ.get("ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR", "")
        if not (source and session and output):
            raise VerifiedIngressNotReady(
                "verified-only startup stopped: explicit historical replay DB, "
                "session and new output directory are required"
            )
        raw = Path(source)
        target = Path(output)
        if (
            not raw.is_absolute() or not raw.is_file()
            or not target.is_absolute()
            or raw.resolve() == target.resolve()
            or not session.startswith("rec_")
            or len(session) > 48
            or (target.exists() and any(target.iterdir()))
        ):
            raise VerifiedIngressNotReady(
                "verified-only startup stopped: invalid closed-session "
                "research replay configuration or existing output directory"
            )
    return chosen
