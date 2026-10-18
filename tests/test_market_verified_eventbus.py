"""Opt-in research-only EventBus raw frame gate; default bus unchanged."""
from __future__ import annotations

import asyncio

import pytest

from core.event_bus import (
    EventBus, RawMarketAdmissionDenied, VerifiedMarketPublisher, VerifiedRawDeliveryError,
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


@pytest.mark.asyncio
async def test_confirmed_raw_waits_for_both_subscriber_coroutines_to_finish():
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    pub = bus.activate_verified_market_only(max_inflight=2)
    gate = asyncio.Event()
    seen = []

    async def first(event):
        seen.append("first")
        await gate.wait()
        seen.append("first_done")

    async def second(event):
        seen.append("second")

    bus.subscribe("md.aggTrade", first)
    bus.subscribe("md.aggTrade", second)
    await bus.start()
    try:
        pending = asyncio.create_task(
            pub.publish_raw_confirmed(
                "md.aggTrade", timeout_s=2.0, frame={"a":1},
            )
        )
        await asyncio.sleep(0.01)
        assert not pending.done()
        gate.set()
        await pending
        await bus.wait_verified_idle(timeout_s=2.0)
        assert set(seen) == {"first","second","first_done"}
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_confirmed_raw_subscriber_failure_refuses_ack_and_poisoned_followup():
    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only()
    async def broken(_event):
        raise RuntimeError("consumer crashed")
    bus.subscribe("md.aggTrade", broken)
    await bus.start()
    try:
        with pytest.raises(VerifiedRawDeliveryError, match="handler failed"):
            await pub.publish_raw_confirmed(
                "md.aggTrade",timeout_s=1,frame={"a":1},
            )
        with pytest.raises(VerifiedRawDeliveryError, match="poisoned"):
            await pub.publish_raw_confirmed(
                "md.aggTrade",timeout_s=1,frame={"a":2},
            )
        with pytest.raises(VerifiedRawDeliveryError, match="failures"):
            await bus.wait_verified_idle()
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_derived_handler_failure_is_visible_at_complete_graph_barrier():
    bus=EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only()
    async def raw(_event):
        await bus.publish("md.ticker",price=1)
    async def derived(_event):
        raise ValueError("derived observer failed")
    bus.subscribe("md.aggTrade", raw)
    bus.subscribe("md.ticker", derived)
    await bus.start()
    try:
        await pub.publish_raw_confirmed("md.aggTrade", frame={"a":1})
        with pytest.raises(VerifiedRawDeliveryError, match="handler failures"):
            await bus.wait_verified_idle(timeout_s=2)
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_verified_credits_are_not_released_on_unknown_timeout():
    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only(max_inflight=1)
    started=asyncio.Event()
    release=asyncio.Event()
    async def blocked(_event):
        started.set()
        await release.wait()
    bus.subscribe("md.aggTrade",blocked)
    await bus.start()
    try:
        with pytest.raises(VerifiedRawDeliveryError,match="outcome unknown"):
            await pub.publish_raw_confirmed(
                "md.aggTrade",timeout_s=0.02,frame={"a":1},
            )
        assert started.is_set()
        assert bus._verified_slots._value == 0
        with pytest.raises(VerifiedRawDeliveryError,match="backpressure"):
            await pub.publish_raw_confirmed(
                "md.aggTrade",timeout_s=0.01,frame={"a":2},
            )
        release.set()
        await bus.wait_verified_idle(timeout_s=2)
        await asyncio.sleep(0)
        assert bus._verified_slots._value == 1
    finally:
        release.set()
        await bus.stop()


@pytest.mark.asyncio
async def test_confirmed_topic_without_handler_is_refused():
    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only()
    await bus.start()
    try:
        with pytest.raises(VerifiedRawDeliveryError,match="no eligible"):
            await pub.publish_raw_confirmed(
                "md.depth",frame={"lastUpdateId":100},
            )
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_shutdown_rejects_unknown_confirmation_instead_of_claiming_ack():
    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only(max_inflight=1)
    waiting=asyncio.Event()
    async def slow(_event):
        waiting.set()
        await asyncio.sleep(30)
    bus.subscribe("md.depth",slow)
    await bus.start()
    task=asyncio.create_task(
        pub.publish_raw_confirmed(
            "md.depth",timeout_s=5,frame={"lastUpdateId":3},
        )
    )
    await asyncio.wait_for(waiting.wait(),1)
    await bus.stop()
    with pytest.raises(VerifiedRawDeliveryError,match="stopped"):
        await task


@pytest.mark.parametrize("n",[-1,0,129,True,1.5,"16"])
def test_verified_inflight_limit_invalid(n):
    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    with pytest.raises(RawMarketAdmissionDenied,match="bound"):
        bus.activate_verified_market_only(max_inflight=n)


@pytest.mark.asyncio
async def test_optin_verified_derived_fence_refuses_forged_direct_publication():
    from core.event_bus import VerifiedDerivedPublisher
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    bus.activate_verified_market_only()
    capability = bus.activate_verified_derived_only()
    with pytest.raises(RawMarketAdmissionDenied, match="capability"):
        await bus.publish(
            "md.ticker", source="verified_closed_session_replay",
            verified_session_id="rec_fake", verified_source_record_id="rec_fake:aggTrade:1",
            verified_source_ordinal=1, verified_source_available_at_ms=1780000000001,
        )
    fake = VerifiedDerivedPublisher(bus, object())
    with pytest.raises(RawMarketAdmissionDenied, match="unverified derived"):
        await fake.publish(
            "md.ticker", source="verified_closed_session_replay",
            verified_session_id="rec_fake", verified_source_record_id="rec_fake:aggTrade:1",
            verified_source_ordinal=1, verified_source_available_at_ms=1780000000001,
        )
    assert bus._queue.empty()
    with pytest.raises(RawMarketAdmissionDenied, match="unverified derived"):
        await capability.publish("md.ticker", source="unknown")
    assert bus._queue.empty()
    with pytest.raises(RawMarketAdmissionDenied, match="fresh protected"):
        bus.activate_verified_derived_only()


@pytest.mark.asyncio
async def test_verified_derived_pipeline_produces_source_bound_events_and_denies_bypass():
    from infrastructure.preprocessing.pipeline import MarketDataPipeline
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    publisher = bus.activate_verified_market_only()
    derived = bus.activate_verified_derived_only()
    MarketDataPipeline(bus, verified_derived_publisher=derived).register()
    received = []
    async def observed(e):
        received.append(e)
    bus.subscribe("md.ticker", observed)
    bus.subscribe("indicator.volume_delta", observed)
    await bus.start()
    try:
        await publisher.publish_raw_confirmed(
            "md.aggTrade",
            frame={"e":"aggTrade","s":"BTCUSDT","a":501,
                   "E":1780000000000,"T":1780000000000,
                   "f":501,"l":501,"p":"101","q":"0.2","m":False},
            source="verified_closed_session_replay",
            verified_session_id="rec_derived_gate",
            verified_source_record_id="rec_derived_gate:aggTrade:1",
            verified_source_ordinal=1,
            verified_source_available_at_ms=1780000000001,
        )
        await bus.wait_verified_idle()
        assert {e.topic for e in received} == {"md.ticker", "indicator.volume_delta"}
        for e in received:
            assert e.payload["verified_session_id"] == "rec_derived_gate"
            assert e.payload["source"] == "verified_closed_session_replay"
        with pytest.raises(RawMarketAdmissionDenied):
            await bus.publish("indicator.volume_delta", source="verified_closed_session_replay")
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_missing_derived_capability_poisoned_handler_in_fenced_research_bus():
    from infrastructure.preprocessing.pipeline import MarketDataPipeline
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    publisher = bus.activate_verified_market_only()
    bus.activate_verified_derived_only()
    MarketDataPipeline(bus).register()  # incorrect wiring deliberately
    await bus.start()
    try:
        with pytest.raises(VerifiedRawDeliveryError, match="handler failed"):
            await publisher.publish_raw_confirmed(
                "md.aggTrade",
                frame={"e":"aggTrade","s":"BTCUSDT","a":500,
                       "E":1780000000000,"T":1780000000000,
                       "f":500,"l":500,"p":"101","q":"0.1","m":False},
                source="verified_closed_session_replay",
                verified_session_id="rec_missing_cap",
                verified_source_record_id="rec_missing_cap:aggTrade:1",
                verified_source_ordinal=1,
                verified_source_available_at_ms=1780000000001,
            )
        with pytest.raises(VerifiedRawDeliveryError, match="handler failures"):
            await bus.wait_verified_idle()
    finally:
        await bus.stop()
