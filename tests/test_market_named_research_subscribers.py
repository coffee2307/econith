"""Independent, source-bound research subscribers: crash, replay, isolation."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from core.event_bus import Event, EventBus, VerifiedRawDeliveryError
from core.mode import QuantMode
from infrastructure.market_named_research_subscribers import (
    OfflineNamedResearchSubscriber, NamedResearchSubscriberError,
    checked_named_recovery_paths,
)
from infrastructure.preprocessing.pipeline import MarketDataPipeline

SESSION = "rec_named_receiver01"
BASE = 1_780_000_000_000
COUNTS = {"md.ticker":2, "indicator.volume_delta":2, "indicator.obi":1}


def event(topic="md.ticker", ordinal=1, source="verified_closed_session_replay",
          value=101, session=SESSION):
    reference = (
        f"{session}:book:555" if topic == "indicator.obi"
        else f"{session}:aggTrade:{100 + ordinal}"
    )
    return Event(topic, {
        "source": source, "verified_session_id": session,
        "verified_source_record_id": reference,
        "verified_source_ordinal": ordinal,
        "verified_source_available_at_ms": BASE + ordinal,
        "price": value, "symbol": "BTCUSDT",
    })


def make(tmp_path, name="research_audit", *, source="verified_closed_session_replay",
         mode=None):
    flag = [QuantMode.SIMULATION] if mode is None else mode
    return OfflineNamedResearchSubscriber(
        tmp_path / (name + ".sqlite"), name=name,
        expected_source=source, mode_provider=lambda: flag[0],
    )


@pytest.mark.asyncio
async def test_two_named_receivers_independent_replay_and_counts(tmp_path):
    a = make(tmp_path, "research_audit")
    b = make(tmp_path, "research_monitor")
    for topic, n in COUNTS.items():
        for ordinal in range(1, n + 1):
            await a.observe(event(topic, ordinal))
            await b.observe(event(topic, ordinal))
    assert a.verify(SESSION, COUNTS).durable_receipts == 5
    assert b.verify(SESSION, COUNTS).durable_receipts == 5
    assert a.replayed_receipts == b.replayed_receipts == 0
    a_again = make(tmp_path, "research_audit")
    for topic, n in COUNTS.items():
        for ordinal in range(1, n + 1):
            await a_again.observe(event(topic, ordinal))
    assert a_again.verify(SESSION, COUNTS).replayed_receipts == 5
    assert make(tmp_path, "research_monitor").verify(SESSION, COUNTS).durable_receipts == 5
    assert a.path != b.path


@pytest.mark.asyncio
async def test_failure_in_one_named_consumer_must_not_advance_other_cursor(tmp_path):
    audit = make(tmp_path, "research_audit")
    monitor = make(tmp_path, "research_monitor")
    await audit.observe(event())
    with pytest.raises(NamedResearchSubscriberError, match="count mismatch"):
        monitor.verify(SESSION, {"md.ticker":1, "indicator.volume_delta":0,
                                 "indicator.obi":0})
    assert audit.verify(SESSION, {"md.ticker":1, "indicator.volume_delta":0,
                                  "indicator.obi":0}).durable_receipts == 1
    assert monitor.verify(SESSION, {"md.ticker":0, "indicator.volume_delta":0,
                                    "indicator.obi":0}).durable_receipts == 0
    # Only the lagging consumer replays its missing durable observation.
    await monitor.observe(event())
    assert monitor.verify(SESSION, {"md.ticker":1, "indicator.volume_delta":0,
                                    "indicator.obi":0}).durable_receipts == 1


@pytest.mark.asyncio
async def test_conflicting_replay_and_out_of_order_fail_closed(tmp_path):
    a = make(tmp_path)
    with pytest.raises(NamedResearchSubscriberError):
        await a.observe(event(source="unverified_legacy"))
    with pytest.raises(Exception, match="ordinal gap"):
        await a.observe(event(ordinal=2))
    await a.observe(event())
    with pytest.raises(Exception, match="conflicting"):
        await a.observe(event(value=999))
    assert a.verify(SESSION, {"md.ticker":1, "indicator.volume_delta":0,
                              "indicator.obi":0}).durable_receipts == 1


def test_wrong_reopen_identity_and_mode_refuse(tmp_path):
    make(tmp_path)
    with pytest.raises(NamedResearchSubscriberError, match="identity"):
        OfflineNamedResearchSubscriber(
            tmp_path / "research_audit.sqlite", name="research_other",
            expected_source="verified_closed_session_replay",
            mode_provider=lambda: QuantMode.SIMULATION,
        )
    with pytest.raises(NamedResearchSubscriberError, match="policy"):
        make(tmp_path, source="verified_sealed_public_microbatch")
    with pytest.raises(RuntimeError, match="SIMULATION"):
        make(tmp_path, name="research_new", mode=[QuantMode.REALITY])
    assert not (tmp_path / "research_new.sqlite").exists()


def test_existing_foreign_original_db_not_mutated(tmp_path):
    p = tmp_path / "original.sqlite"
    with sqlite3.connect(p) as db:
        db.execute("CREATE TABLE evidence(x INTEGER)")
        db.execute("INSERT INTO evidence VALUES (123)")
    before = p.read_bytes()
    with pytest.raises(NamedResearchSubscriberError, match="identity"):
        OfflineNamedResearchSubscriber(
            p, name="research_audit",
            expected_source="verified_closed_session_replay",
            mode_provider=lambda: QuantMode.SIMULATION,
        )
    assert p.read_bytes() == before


def test_recovery_paths_require_separate_existing_named_ledgers(tmp_path):
    orig = tmp_path / "raw.sqlite"
    primary = tmp_path / "primary.sqlite"
    output = tmp_path / "fresh"
    names = ("research_audit", "research_monitor")
    a = make(tmp_path, "research_audit")
    b = make(tmp_path, "research_monitor")
    valid = checked_named_recovery_paths(
        output_dir=output, original_db=orig,
        primary_receipts=primary, names=names,
        recovery={"research_audit":a.path.resolve(), "research_monitor":b.path.resolve()},
    )
    assert len(set(valid.values())) == 2
    for wrong in (
        {"research_audit":a.path.resolve(), "research_monitor":a.path.resolve()},
        {"research_audit":orig.resolve(), "research_monitor":b.path.resolve()},
        {"research_audit":a.path.resolve()},
        {"research_audit":a.path.resolve(),
         "research_monitor":output/"research_monitor.sqlite"},
    ):
        with pytest.raises(NamedResearchSubscriberError):
            checked_named_recovery_paths(
                output_dir=output, original_db=orig,
                primary_receipts=primary, names=names, recovery=wrong,
            )


@pytest.mark.asyncio
async def test_real_protected_bus_named_consumer_receipts_survive_reopen(tmp_path):
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    publisher = bus.activate_verified_market_only()
    MarketDataPipeline(bus).register()
    a = make(tmp_path, "research_audit")
    b = make(tmp_path, "research_monitor")
    a.register(bus)
    b.register(bus)
    with pytest.raises(NamedResearchSubscriberError, match="duplicate"):
        a.register(bus)
    await bus.start()
    try:
        for n in (1, 2):
            await publisher.publish_raw_confirmed(
                "md.aggTrade",
                frame={"e":"aggTrade","s":"BTCUSDT","a":n,
                       "E":BASE,"T":BASE,"f":n,"l":n,
                       "p":"101","q":"0.1","m":False},
                source="verified_closed_session_replay",
                verified_session_id=SESSION,
                verified_source_record_id=f"{SESSION}:aggTrade:{100+n}",
                verified_source_ordinal=n,
                verified_source_available_at_ms=BASE+n,
            )
        await bus.wait_verified_idle()
        expected = {"md.ticker":2, "indicator.volume_delta":2, "indicator.obi":0}
        assert a.verify(SESSION, expected).durable_receipts == 4
        assert b.verify(SESSION, expected).durable_receipts == 4
        assert make(tmp_path, "research_audit").verify(SESSION, expected).durable_receipts == 4
    finally:
        await bus.stop()


def test_actual_python_hard_exit_mid_named_receipt_then_reopen(tmp_path):
    # Receiver terminates without Python cleanup after its SQLite COMMIT.
    db = tmp_path / "research_audit.sqlite"
    program = """
import asyncio,os,sys
from core.event_bus import Event
from core.mode import QuantMode
from infrastructure.market_named_research_subscribers import OfflineNamedResearchSubscriber
x=OfflineNamedResearchSubscriber(sys.argv[1],name='research_audit',
    expected_source='verified_closed_session_replay',
    mode_provider=lambda:QuantMode.SIMULATION)
asyncio.run(x.observe(Event('md.ticker', {
    'source':'verified_closed_session_replay',
    'verified_session_id':'rec_named_receiver01',
    'verified_source_record_id':'rec_named_receiver01:aggTrade:101',
    'verified_source_ordinal':1,
    'verified_source_available_at_ms':1780000000001,
    'price':101})))
os._exit(47)
"""
    p = subprocess.run(
        [sys.executable, "-c", program, str(db)],
        capture_output=True, text=True, timeout=15,
    )
    assert p.returncode == 47, p.stderr
    recovered = make(tmp_path, "research_audit")
    assert recovered.verify(SESSION, {"md.ticker":1, "indicator.volume_delta":0,
                                      "indicator.obi":0}).durable_receipts == 1
    asyncio.run(recovered.observe(Event("md.ticker", {
        "source":"verified_closed_session_replay",
        "verified_session_id":SESSION,
        "verified_source_record_id":f"{SESSION}:aggTrade:101",
        "verified_source_ordinal":1,
        "verified_source_available_at_ms":BASE+1,
        "price":101,
    })))
    assert recovered.replayed_receipts == 1
