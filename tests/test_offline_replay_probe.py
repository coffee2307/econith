"""EventBus integration for a research-only replay probe; zero broker calls."""
from __future__ import annotations

import asyncio

from core.event_bus import DOMAIN_QUANT, Event, EventBus
from core.mode import QuantMode
from quant.offline_admission import OfflineAdmissionLedger
from quant.offline_replay_probe import OfflineReplayProbe


def test_true_eventbus_duplicate_is_reported_but_never_dispatched(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "probe.sqlite")
    probe = OfflineReplayProbe(ledger, mode_provider=lambda: QuantMode.SIMULATION)
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    outcomes = []

    async def handler(event):
        outcomes.append(await probe.observe(event))

    bus.subscribe("order.intent", handler, domain=DOMAIN_QUANT)

    async def scenario():
        await bus.start()
        try:
            payload = {
                "intent_id": "signal-001:BTCUSDT:leg-0",
                "symbol": "BTCUSDT",
                "side": "BUY",
                "quantity": 0.0125,
                "mode": "SIMULATION",
            }
            await bus.publish("order.intent", **payload)
            await bus.publish("order.intent", **payload)
            for _ in range(75):
                if len(outcomes) == 2:
                    break
                await asyncio.sleep(0.01)
        finally:
            await bus.stop()

    asyncio.run(scenario())
    assert [o.result for o in outcomes] == ["NEW", "DUPLICATE"]
    assert outcomes[0].client_order_id == outcomes[1].client_order_id
    assert all(o.transmitted_to_broker is False for o in outcomes)


def test_persisted_replay_detection_after_process_restart(tmp_path):
    path = tmp_path / "probe.sqlite"
    intent = Event(topic="order.intent", payload={
        "intent_id": "signal:3", "symbol": "BTCUSDT",
        "side": "SELL", "quantity": 0.01,
    })

    async def do(probe):
        return await probe.observe(intent)

    first = OfflineReplayProbe(
        OfflineAdmissionLedger(path), mode_provider=lambda: QuantMode.SIMULATION
    )
    second = OfflineReplayProbe(
        OfflineAdmissionLedger(path), mode_provider=lambda: QuantMode.SIMULATION
    )
    assert asyncio.run(do(first)).result == "NEW"
    assert asyncio.run(do(second)).result == "DUPLICATE"


def test_missing_origin_id_is_quarantined_without_fallback_hash(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "probe.sqlite")
    probe = OfflineReplayProbe(ledger, mode_provider=lambda: QuantMode.SIMULATION)
    item = Event(topic="order.intent", payload={
        "symbol": "BTCUSDT", "side": "BUY", "quantity": 0.01
    })
    result = asyncio.run(probe.observe(item))
    assert result.result == "QUARANTINED"
    assert result.reason == "missing_origin_id"


def test_reality_mode_never_admits_or_publishes_broker_order(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "probe.sqlite")
    probe = OfflineReplayProbe(ledger, mode_provider=lambda: QuantMode.REALITY)
    event = Event(topic="order.intent", payload={
        "intent_id": "live-001", "symbol": "BTCUSDT",
        "side": "BUY", "quantity": 0.01,
    })
    result = asyncio.run(probe.observe(event))
    assert result.result == "QUARANTINED"
    assert result.reason == "simulation_only"
    assert ledger.get("live-001") is None
    assert not result.transmitted_to_broker


def test_same_id_with_changed_content_is_quarantined(tmp_path):
    ledger = OfflineAdmissionLedger(tmp_path / "probe.sqlite")
    probe = OfflineReplayProbe(ledger, mode_provider=lambda: QuantMode.SIMULATION)
    baseline = {"intent_id": "s-1", "symbol": "BTCUSDT", "quantity": 0.01}
    assert asyncio.run(probe.observe(Event(topic="order.intent", payload=baseline))).result == "NEW"
    changed = {**baseline, "quantity": 999}
    second = asyncio.run(probe.observe(Event(topic="order.intent", payload=changed)))
    assert second.result == "QUARANTINED"
    assert second.client_order_id is None
