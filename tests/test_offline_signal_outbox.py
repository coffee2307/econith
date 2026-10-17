"""Durable producer-origin ID and outbox tests (SIMULATION only)."""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from core.mode import QuantMode
from quant.offline_signal_outbox import (
    OfflineSignalOutbox, derive_leg_intent_id,
)


class CapturingBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, **payload):
        self.events.append((topic, payload))


def test_staged_signal_has_durable_id_and_manual_pending_replay(tmp_path):
    filename = tmp_path / "signals.sqlite"
    box = OfflineSignalOutbox(filename, namespace="worldpaper")
    staged = box.stage({"action": "LONG", "direction": 0.5})
    assert staged.status == "STAGED"
    assert staged.signal_id == "sig:worldpaper:1"
    assert staged.payload["signal_id"] == staged.signal_id
    restarted = OfflineSignalOutbox(filename, namespace="worldpaper")
    assert restarted.pending()[0].payload == staged.payload
    bus = CapturingBus()
    asyncio.run(restarted.publish_to_simulation(bus, staged, mode=QuantMode.SIMULATION))
    assert bus.events == [("ai.signal", staged.payload)]
    assert restarted.pending()[0].status == "QUEUED"
    # EventBus.publish only queues; it does not acknowledge consumption.
    restarted_again = OfflineSignalOutbox(filename, namespace="worldpaper")
    assert restarted_again.pending()[0].signal_id == staged.signal_id
    restarted_again.acknowledge(staged.signal_id)
    assert restarted_again.pending() == []


def test_publish_failure_does_not_lose_committed_signal(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "signals.sqlite")
    signal = box.stage({"action": "SHORT"})

    class FailingBus:
        async def publish(self, *args, **kwargs):
            raise ConnectionError("simulated queue fault")

    with pytest.raises(ConnectionError):
        asyncio.run(box.publish_to_simulation(FailingBus(), signal, mode=QuantMode.SIMULATION))
    assert box.get(signal.signal_id).status == "STAGED"
    bus = CapturingBus()
    asyncio.run(box.publish_to_simulation(bus, signal, mode=QuantMode.SIMULATION))
    assert len(bus.events) == 1
    assert box.get(signal.signal_id).status == "QUEUED"


def test_outbox_never_publishes_in_reality_and_does_not_forge_ids(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "signals.sqlite")
    signal = box.stage({"action": "FLAT"})
    bus = CapturingBus()
    with pytest.raises(RuntimeError, match="SIMULATION"):
        asyncio.run(box.publish_to_simulation(bus, signal, mode=QuantMode.REALITY))
    assert bus.events == []
    assert box.get(signal.signal_id).status == "STAGED"
    with pytest.raises(ValueError):
        box.stage({"signal_id": "injected", "action": "FLAT"})
    with pytest.raises(ValueError):
        box.stage({"direction": float("nan")})
    assert len(box.pending()) == 1


def test_outbox_rejects_unsupported_ack_and_envelope_mutation(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "signals.sqlite")
    row = box.stage({"action": "LONG"})
    with pytest.raises(ValueError, match="only a queued"):
        box.acknowledge(row.signal_id)
    forged = SimpleNamespace(signal_id=row.signal_id, payload={"action": "SHORT"})
    with pytest.raises(ValueError, match="altered"):
        asyncio.run(box.publish_to_simulation(CapturingBus(), forged, mode=QuantMode.SIMULATION))
    with pytest.raises(ValueError):
        box.get("sig:other:1")


def test_leg_identity_is_per_source_leg_and_symbol():
    base = derive_leg_intent_id("sig:worldpaper:17", 0, "BTCUSDT")
    assert base == derive_leg_intent_id("sig:worldpaper:17", 0, "BTCUSDT")
    assert base != derive_leg_intent_id("sig:worldpaper:17", 1, "BTCUSDT")
    assert base != derive_leg_intent_id("sig:worldpaper:18", 0, "BTCUSDT")
    assert base != derive_leg_intent_id("sig:worldpaper:17", 0, "ETHUSDT")
    for invalid in ("", "with space", "bad/../path"):
        with pytest.raises(ValueError):
            derive_leg_intent_id(invalid, 0, "BTCUSDT")


def test_predictor_uses_opt_in_outbox_but_default_path_is_unchanged(tmp_path):
    from ai.inference.predictor import Predictor
    bus = CapturingBus()
    predictor = Predictor.__new__(Predictor)
    predictor._bus = bus
    predictor._mode = SimpleNamespace(mode=QuantMode.SIMULATION)
    predictor._offline_signal_outbox = OfflineSignalOutbox(tmp_path / "signals.sqlite")
    asyncio.run(predictor._publish_advisory_signal({"action": "LONG", "direction": 0.5}))
    assert len(bus.events) == 1
    signal_id = bus.events[0][1]["signal_id"]
    assert predictor._offline_signal_outbox.get(signal_id).status == "QUEUED"
    predictor._offline_signal_outbox = None
    asyncio.run(predictor._publish_advisory_signal({"action": "FLAT"}))
    assert bus.events[1] == ("ai.signal", {"action": "FLAT"})


def test_opted_in_predictor_rejects_reality_even_before_outbox_write(tmp_path):
    from ai.inference.predictor import Predictor
    bus = CapturingBus()
    predictor = Predictor.__new__(Predictor)
    predictor._bus = bus
    predictor._mode = SimpleNamespace(mode=QuantMode.REALITY)
    predictor._offline_signal_outbox = OfflineSignalOutbox(tmp_path / "signals.sqlite")
    with pytest.raises(RuntimeError, match="SIMULATION-only"):
        asyncio.run(predictor._publish_advisory_signal({"action": "LONG"}))
    assert bus.events == []
    assert predictor._offline_signal_outbox.pending() == []


def test_aibridge_propagates_source_id_to_each_simulated_order_leg(tmp_path):
    from econith_quant.bridge.ai_bridge import AIBridge

    async def process(source_id):
        bus = CapturingBus()
        bridge = AIBridge(bus)
        bridge._mode = "NORMAL"
        bridge._min_delta = 0.001
        await bridge._on_signal(SimpleNamespace(payload={
            "signal_id": source_id,
            "action": "LONG",
            "direction": 0.8,
            "confidence": 0.9,
            "regime": "TREND",
        }))
        return [row for topic, row in bus.events if topic == "order.intent"]

    one = asyncio.run(process("sig:worldpaper:43"))
    replay = asyncio.run(process("sig:worldpaper:43"))
    assert one and len(one) == len(replay)
    assert all(x["signal_id"] == "sig:worldpaper:43" for x in one)
    assert [x["intent_id"] for x in one] == [x["intent_id"] for x in replay]
    assert len({x["intent_id"] for x in one}) == len(one)


def test_aibridge_rejects_forged_origin_id_before_exposure_mutation():
    from econith_quant.bridge.ai_bridge import AIBridge
    bus = CapturingBus()
    bridge = AIBridge(bus)
    original_exposure = bridge._target_exposure
    asyncio.run(bridge._on_signal(SimpleNamespace(payload={
        "signal_id": "untrusted/identifier",
        "action": "LONG",
        "direction": 0.8,
        "confidence": 0.9,
    })))
    assert bridge._target_exposure == original_exposure
    assert not [x for x in bus.events if x[0] == "order.intent"]
    assert any(topic == "ai.veto" for topic, _ in bus.events)
