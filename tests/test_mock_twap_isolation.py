"""Legacy mock TWAP may never contaminate REALITY order history."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from core.mode import QuantMode
from econith_quant.bridge import exchange_bridge as module


class Bus:
    def __init__(self):
        self.events = []
        self.subscriptions = []

    def subscribe(self, name, handler, **kwargs):
        self.subscriptions.append(name)

    async def publish(self, name, **payload):
        self.events.append((name, payload))


class State:
    def __init__(self):
        self.rows = []

    def record(self, **kwargs):
        self.rows.append(kwargs)


def test_mock_twap_enabled_but_reality_never_fabricates_fill(monkeypatch) -> None:
    bus, state = Bus(), State()
    monkeypatch.setattr(module, "current_mode", lambda: QuantMode.REALITY)
    bridge = module.ExchangeBridge(bus, state=state, enabled=True)
    bridge.register()
    assert "order.intent" in bus.subscriptions  # active future SIM switch
    intent = SimpleNamespace(payload={
        "symbol": "BTCUSDT", "side": "BUY",
        "quantity": 0.025, "reason": "simulation-only",
    })
    asyncio.run(bridge._on_intent(intent))
    assert bus.events == []
    assert state.rows == []


def test_mock_twap_only_emits_labeled_simulation_lifecycle(monkeypatch) -> None:
    bus, state = Bus(), State()
    active = [QuantMode.SIMULATION]
    monkeypatch.setattr(module, "current_mode", lambda: active[0])
    bridge = module.ExchangeBridge(bus, state=state, enabled=True)
    intent = SimpleNamespace(payload={
        "symbol": "BTCUSDT", "side": "BUY",
        "quantity": 0.025, "reason": "test",
    })

    asyncio.run(bridge._on_intent(intent))
    updates = [payload for topic, payload in bus.events if topic == "order.update"]
    assert [p["status"] for p in updates] == ["SUBMITTED", "FILLED"]
    assert all(p["mode"] == "SIMULATION"
               and p["simulated"] is True
               and p["source"] == "mock_twap" for p in updates)
    assert state.rows and all(row["status"] == "FILLED" for row in state.rows)

    rows_before = len(state.rows)
    events_before = len(bus.events)
    active[0] = QuantMode.REALITY
    asyncio.run(bridge._on_intent(intent))
    assert len(state.rows) == rows_before
    assert len(bus.events) == events_before


def test_mock_twap_disabled_in_simulation_is_inert(monkeypatch) -> None:
    monkeypatch.setattr(module, "current_mode", lambda: QuantMode.SIMULATION)
    bus, state = Bus(), State()
    bridge = module.ExchangeBridge(bus, state=state, enabled=False)
    asyncio.run(bridge._on_intent(SimpleNamespace(
        payload={"symbol": "BTCUSDT", "side": "SELL", "quantity": 0.025}
    )))
    assert bus.events == [] and state.rows == []
