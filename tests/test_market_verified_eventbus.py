"""Opt-in research-only EventBus raw frame gate; default bus unchanged."""
from __future__ import annotations

import asyncio

import pytest

from core.event_bus import (
    EventBus, RawMarketAdmissionDenied, VerifiedMarketPublisher,
)
from core.mode import QuantMode


@pytest.mark.asyncio
async def test_legacy_bus_publishes_legacy_raw_topics_unchanged():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    seen = []
    ready = asyncio.Event()

    async def handler(event):
        seen.append(event.payload["source"])
        ready.set()

    bus.subscribe("md.aggTrade", handler)
    await bus.start()
    try:
        await bus.publish("md.aggTrade", frame={"a":1}, source="legacy")
        await asyncio.wait_for(ready.wait(), 2)
        assert seen == ["legacy"]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_optin_verified_bus_rejects_raw_publish_even_when_source_claims_verified():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    bus.activate_verified_market_only()
    for topic in ("md.aggTrade","md.depth"):
        with pytest.raises(RawMarketAdmissionDenied, match="capability required"):
            await bus.publish(topic, frame={"e":"aggTrade"},
                              source="verified", verified=True)
    assert bus._queue.empty()


@pytest.mark.asyncio
async def test_valid_publisher_capability_delivers_only_protected_raw_topics():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    holder = bus.activate_verified_market_only()
    seen = []
    done = asyncio.Event()

    async def consumer(event):
        seen.append((event.topic,event.payload["source"]))
        if len(seen) == 2:
            done.set()

    bus.subscribe("md.aggTrade", consumer)
    bus.subscribe("md.depth", consumer)
    await bus.start()
    try:
        await holder.publish_raw("md.aggTrade", frame={"a":101}, source="research")
        await holder.publish_raw("md.depth", frame={"lastUpdateId":10}, source="research")
        await asyncio.wait_for(done.wait(), 2)
        assert seen == [("md.aggTrade","research"),("md.depth","research")]
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_forged_capability_is_rejected():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    bus.activate_verified_market_only()
    forged = VerifiedMarketPublisher(bus, object())
    with pytest.raises(RawMarketAdmissionDenied):
        await forged.publish_raw("md.aggTrade", frame={"a":1})
    assert bus._queue.empty()


@pytest.mark.asyncio
async def test_derived_ticker_not_blocked_by_raw_topic_admission():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    bus.activate_verified_market_only()
    await bus.publish("md.ticker", symbol="BTCUSDT", price=100)
    assert bus._queue.qsize() == 1


@pytest.mark.asyncio
async def test_verified_capability_cannot_publish_unlisted_market_topic():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    holder = bus.activate_verified_market_only()
    with pytest.raises(RawMarketAdmissionDenied):
        await holder.publish_raw("order.intent", quantity=10)
    assert bus._queue.empty()


def test_activate_reality_denied_before_market_traffic():
    bus = EventBus(mode_provider=lambda: QuantMode.REALITY)
    with pytest.raises(RawMarketAdmissionDenied, match="SIMULATION"):
        bus.activate_verified_market_only()


@pytest.mark.asyncio
async def test_switching_to_reality_denies_trusted_raw_market_emit():
    current = [QuantMode.SIMULATION]
    bus = EventBus(mode_provider=lambda: current[0])
    holder = bus.activate_verified_market_only()
    current[0] = QuantMode.REALITY
    with pytest.raises(RawMarketAdmissionDenied):
        await holder.publish_raw("md.aggTrade", frame={"a":1})
    assert bus._queue.empty()


@pytest.mark.asyncio
async def test_late_activation_after_bus_start_or_pending_events_denied():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    await bus.publish("system.log", message="pending")
    with pytest.raises(RawMarketAdmissionDenied, match="before bus traffic"):
        bus.activate_verified_market_only()
    bus2 = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    await bus2.start()
    try:
        with pytest.raises(RawMarketAdmissionDenied, match="before bus traffic"):
            bus2.activate_verified_market_only()
    finally:
        await bus2.stop()


def test_duplicate_grant_denied_without_second_capability():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    a = bus.activate_verified_market_only()
    assert isinstance(a, VerifiedMarketPublisher)
    with pytest.raises(RawMarketAdmissionDenied, match="already issued"):
        bus.activate_verified_market_only()
