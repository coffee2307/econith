"""Isolated EventBus probe for idempotency experiments.

This is NOT connected to main.py and cannot submit broker orders.
It observes a simulated order.intent, records exactly one first admission,
and reports outcomes for diagnostics. A replay never becomes a new order.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable

from core.event_bus import Event
from core.mode import QuantMode, current_mode
from quant.offline_admission import (
    IntentConflict, OfflineAdmissionLedger,
)


@dataclass(frozen=True)
class ReplayProbeOutcome:
    intent_id: str
    result: str  # NEW | DUPLICATE | QUARANTINED
    client_order_id: str | None
    reason: str
    transmitted_to_broker: bool = False


class OfflineReplayProbe:
    """Side-effect limited to local durable admission; no exchange adapter."""

    def __init__(
        self,
        ledger: OfflineAdmissionLedger,
        *,
        mode_provider: Callable[[], QuantMode] = current_mode,
    ) -> None:
        self._ledger = ledger
        self._mode_provider = mode_provider

    async def observe(self, event: Event) -> ReplayProbeOutcome:
        raw = event.payload.get("intent_id", "")
        intent_id = raw if isinstance(raw, str) else ""
        if event.topic != "order.intent":
            return ReplayProbeOutcome(intent_id, "QUARANTINED", None, "wrong_topic")
        if self._mode_provider() is not QuantMode.SIMULATION:
            return ReplayProbeOutcome(intent_id, "QUARANTINED", None, "simulation_only")
        if not intent_id:
            return ReplayProbeOutcome(intent_id, "QUARANTINED", None, "missing_origin_id")
        try:
            record = self._ledger.admit(intent_id, event.payload)
        except (IntentConflict, ValueError):
            return ReplayProbeOutcome(intent_id, "QUARANTINED", None, "invalid_or_conflicting_intent")
        except sqlite3.Error:
            return ReplayProbeOutcome(intent_id, "QUARANTINED", None, "ledger_unavailable")
        return ReplayProbeOutcome(
            intent_id,
            "NEW" if record.created else "DUPLICATE",
            record.client_order_id,
            "recorded_no_dispatch" if record.created else "replay_no_dispatch",
        )
