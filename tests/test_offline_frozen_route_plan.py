"""Phase 0.5: immutable SIMULATION plans survive replay and mode transitions."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.mode import QuantMode
from econith_quant.bridge.ai_bridge import AIBridge
from quant.offline_ack_bridge import AdmissionReceiptError, OfflineSignalAckBridge
from quant.offline_admission import OfflineAdmissionLedger, fingerprint
from quant.offline_signal_outbox import OfflineSignalOutbox, derive_leg_intent_id


class CapturingBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, **payload):
        self.events.append((topic, payload))


def setup(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "outbox.sqlite", namespace="freeze")
    source = box.stage({
        "action": "LONG", "direction": 0.8, "confidence": 0.9, "regime": "TREND",
    })
    bus = CapturingBus()
    mode = [QuantMode.SIMULATION]
    bridge = AIBridge(
        bus, offline_signal_outbox=box,
        simulation_mode_provider=lambda: mode[0],
    )
    bridge._mode = "NORMAL"
    bridge._min_delta = 0.001
    return box, source, bus, bridge, mode


def dispatch(bridge, source):
    asyncio.run(bridge._on_signal(SimpleNamespace(payload=source.payload)))


def intents(bus):
    return [dict(payload) for topic, payload in bus.events if topic == "order.intent"]


def test_snapshot_committed_before_publication_and_replay_ignores_mutable_sizing(tmp_path):
    box, source, bus, bridge, mode = setup(tmp_path)
    dispatch(bridge, source)
    original = intents(bus)
    stored = box.frozen_route_plan(source.signal_id)
    assert original and stored["intents"] == original
    assert stored["route"] == next(x for topic, x in bus.events if topic == "quant.route.plan")

    # New process: price, in-memory exposure, and derisk have all changed.
    restarted_box = OfflineSignalOutbox(box.path, namespace="freeze")
    new_bus = CapturingBus()
    new_bridge = AIBridge(
        new_bus, offline_signal_outbox=restarted_box,
        simulation_mode_provider=lambda: mode[0],
    )
    new_bridge._mode = "NORMAL"
    new_bridge._target_exposure = 0.72
    new_bridge._marks["BTCUSDT"] = 110_000.0
    new_bridge._derisk = 0.05
    dispatch(new_bridge, source)
    assert intents(new_bus) == original
    assert new_bus.events[0][1] == stored["route"]
    assert restarted_box.frozen_route_plan(source.signal_id) == stored
    # Replay never overwrites local exposure state from an old signal.
    assert new_bridge._target_exposure == 0.72


def test_frozen_plan_cannot_be_overwritten_or_created_for_missing_source(tmp_path):
    box, source, bus, bridge, _ = setup(tmp_path)
    dispatch(bridge, source)
    saved = box.frozen_route_plan(source.signal_id)
    assert box.freeze_route_plan(source.signal_id, saved["route"], saved["intents"]) == saved
    changed = [dict(x) for x in saved["intents"]]
    changed[0]["quantity"] *= 2
    with pytest.raises(ValueError, match="conflict"):
        box.freeze_route_plan(source.signal_id, saved["route"], changed)
    missing_id = "sig:freeze:123456"
    absent_legs = []
    for index, old in enumerate(saved["intents"]):
        missing_leg = dict(old)
        missing_leg["signal_id"] = missing_id
        missing_leg["intent_id"] = derive_leg_intent_id(
            missing_id, index, missing_leg["symbol"]
        )
        absent_legs.append(missing_leg)
    with pytest.raises(ValueError, match="missing"):
        box.freeze_route_plan(missing_id, saved["route"], absent_legs)
    assert box.frozen_route_plan(source.signal_id) == saved


def test_ack_inbox_rejects_changed_first_delivery_against_frozen_source(tmp_path):
    box, source, bus, bridge, _ = setup(tmp_path)
    dispatch(bridge, source)
    frozen = box.frozen_route_plan(source.signal_id)
    ledger = OfflineAdmissionLedger(tmp_path / "inbox.sqlite")
    inbox = OfflineSignalAckBridge(
        CapturingBus(), box, ledger, mode_provider=lambda: QuantMode.SIMULATION,
    )
    swapped = dict(frozen["intents"][0])
    swapped["quantity"] += 0.01
    with pytest.raises(AdmissionReceiptError, match="immutable frozen plan"):
        inbox.admit_leg(swapped)
    assert ledger.get(swapped["intent_id"]) is None
    for index, leg in enumerate(frozen["intents"]):
        assert inbox.admit_leg(leg) is (index == len(frozen["intents"]) - 1)
    assert inbox.is_complete(
        source.signal_id, fingerprint(source.payload), len(frozen["intents"]),
    )


def test_sentinel_and_reality_transitions_block_even_cached_replay(tmp_path):
    box, source, bus, bridge, mode = setup(tmp_path)
    dispatch(bridge, source)
    bus.events.clear()
    bridge._mode = "FROZEN"
    dispatch(bridge, source)
    assert not intents(bus)
    assert any(t == "ai.veto" for t, _ in bus.events)
    bus.events.clear()
    bridge._mode = "REDUCE_ONLY"
    dispatch(bridge, source)
    assert not intents(bus)
    bus.events.clear()
    bridge._mode = "NORMAL"
    mode[0] = QuantMode.REALITY
    dispatch(bridge, source)
    assert not intents(bus)
    assert any(t == "ai.veto" for t, _ in bus.events)


def test_stored_source_mismatch_and_post_ack_replay_are_refused(tmp_path):
    box, source, bus, bridge, _ = setup(tmp_path)
    dispatch(bridge, source)
    bus.events.clear()
    changed_signal = SimpleNamespace(payload={**source.payload, "direction": 0.2})
    asyncio.run(bridge._on_signal(changed_signal))
    assert not intents(bus)
    box.mark_queued(source.signal_id)
    box.acknowledge(source.signal_id)
    bus.events.clear()
    dispatch(bridge, source)
    assert not intents(bus)


def test_opt_in_requires_explicit_simulation_mode_provider(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "outbox.sqlite")
    with pytest.raises(ValueError, match="mode provider"):
        AIBridge(CapturingBus(), offline_signal_outbox=box)
