"""Durable source-bound derived observer ACK tests; no network/orders."""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.market_derived_research_receipts import (
    DerivedResearchReceiptError, OfflineDerivedResearchReceiptLedger,
)


def ledger(tmp_path, mode=None):
    return OfflineDerivedResearchReceiptLedger(
        tmp_path / "derived.sqlite",
        mode_provider=mode or (lambda: QuantMode.SIMULATION),
    )


def accept(db, ordinal=1, *, topic="md.ticker", value=101.25,
           session="rec_fixture01", source=None, crash=False):
    return db.accept(
        topic=topic, session_id=session, ordinal=ordinal,
        source_record_id=source or f"{session}:aggTrade:{100+ordinal}",
        source_available_at_ms=1_780_000_000_000+ordinal,
        projection={"symbol":"BTCUSDT","price":value},
        inject_crash_before_commit=crash,
    )


def test_durable_receipts_are_ordered_source_bound_and_survive_reopen(tmp_path):
    d=ledger(tmp_path)
    first=accept(d)
    second=accept(d,2)
    assert not first.replayed and not second.replayed
    reopened=ledger(tmp_path)
    assert reopened.verified_count("rec_fixture01","md.ticker")==2
    assert reopened.verified_count("rec_fixture01","indicator.obi")==0
    reissued=accept(reopened)
    assert reissued.replayed and reissued.receipt_id==first.receipt_id
    assert reopened.verified_count("rec_fixture01","md.ticker")==2


def test_crash_before_commit_rolls_back_both_cursor_and_receipt(tmp_path):
    d=ledger(tmp_path)
    with pytest.raises(DerivedResearchReceiptError,match="injected crash"):
        accept(d,crash=True)
    reopened=ledger(tmp_path)
    assert reopened.verified_count("rec_fixture01","md.ticker")==0
    assert accept(reopened).ordinal==1
    assert reopened.verified_count("rec_fixture01","md.ticker")==1


def test_replay_committed_before_sender_observed_ack_is_idempotent(tmp_path):
    d=ledger(tmp_path)
    received=accept(d)
    # Simulate process dying after transaction COMMIT but before replying.
    after=ledger(tmp_path)
    recovered=accept(after)
    assert recovered.replayed
    assert recovered.receipt_id==received.receipt_id
    assert after.verified_count("rec_fixture01","md.ticker")==1


def test_conflicting_same_ordinal_replay_refused_without_cursor_change(tmp_path):
    d=ledger(tmp_path)
    accept(d)
    with pytest.raises(DerivedResearchReceiptError,match="conflicting"):
        accept(d,value=999)
    with pytest.raises(DerivedResearchReceiptError,match="conflicting"):
        accept(d,source="rec_fixture01:aggTrade:other")
    assert ledger(tmp_path).verified_count("rec_fixture01","md.ticker")==1


def test_out_of_order_or_duplicate_source_across_ordinals_is_refused(tmp_path):
    d=ledger(tmp_path)
    with pytest.raises(DerivedResearchReceiptError,match="ordinal gap"):
        accept(d,3)
    accept(d,1)
    with pytest.raises(DerivedResearchReceiptError):
        accept(d,2,source="rec_fixture01:aggTrade:101")
    assert d.verified_count("rec_fixture01","md.ticker")==1


def test_topics_and_sessions_have_isolated_consumer_cursors(tmp_path):
    d=ledger(tmp_path)
    accept(d,1)
    accept(d,1,topic="indicator.volume_delta")
    accept(d,1,topic="indicator.obi",source="rec_fixture01:book:5000")
    accept(d,1,session="rec_fixture02")
    assert d.verified_count("rec_fixture01","md.ticker")==1
    assert d.verified_count("rec_fixture01","indicator.obi")==1
    assert d.verified_count("rec_fixture02","md.ticker")==1


@pytest.mark.parametrize("overrides", [
    {"topic":"md.depth"},
    {"session":"wrong"},
    {"ordinal":0},
    {"ordinal":True},
    {"source":"rec_else:aggTrade:101"},
])
def test_scope_restrictions_fail_closed(tmp_path,overrides):
    d=ledger(tmp_path)
    with pytest.raises(DerivedResearchReceiptError):
        accept(d,**overrides)
    assert d.verified_count("rec_fixture01","md.ticker")==0


def test_bad_float_and_unserializable_projection_denied(tmp_path):
    d=ledger(tmp_path)
    for value in (float("nan"), float("inf"), object()):
        with pytest.raises(DerivedResearchReceiptError):
            accept(d,value=value)
    assert d.verified_count("rec_fixture01","md.ticker")==0


def test_integrity_verifies_stored_json_hash_and_cursor(tmp_path):
    d=ledger(tmp_path)
    accept(d)
    with sqlite3.connect(d.path) as conn:
        conn.execute("""
            UPDATE offline_research_derived_receipts
            SET projection_json='{"price":1234567}'
            WHERE session_id='rec_fixture01' AND consumer_topic='md.ticker'
        """)
    with pytest.raises(DerivedResearchReceiptError,match="corruption"):
        ledger(tmp_path).verified_count("rec_fixture01","md.ticker")


def test_cursor_corruption_is_detected(tmp_path):
    d=ledger(tmp_path)
    accept(d)
    with sqlite3.connect(d.path) as conn:
        conn.execute("""
            UPDATE offline_research_derived_cursors SET last_ordinal=7
        """)
    with pytest.raises(DerivedResearchReceiptError,match="disagrees"):
        ledger(tmp_path).verified_count("rec_fixture01","md.ticker")


def test_reality_mode_refuses_open_or_ack(tmp_path):
    mode=[QuantMode.SIMULATION]
    d=ledger(tmp_path,lambda:mode[0])
    mode[0]=QuantMode.REALITY
    with pytest.raises(RuntimeError,match="SIMULATION"):
        accept(d)
    with pytest.raises(RuntimeError,match="SIMULATION"):
        d.verified_count("rec_fixture01","md.ticker")
    with pytest.raises(RuntimeError,match="SIMULATION"):
        ledger(tmp_path,lambda:QuantMode.REALITY)


@pytest.mark.asyncio
async def test_pipeline_propagates_verified_lineage_to_derived_only(tmp_path):
    import asyncio
    from core.event_bus import EventBus
    from infrastructure.preprocessing.pipeline import MarketDataPipeline

    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    publisher=bus.activate_verified_market_only()
    pipeline=MarketDataPipeline(bus)
    pipeline.register()
    derived=[]
    async def receive(event):
        derived.append(event)
    bus.subscribe("md.ticker",receive)
    bus.subscribe("indicator.volume_delta",receive)
    await bus.start()
    try:
        frame={"e":"aggTrade","s":"BTCUSDT","a":500,"E":1_780_000_000_010,
               "T":1_780_000_000_000,"f":500,"l":500,"p":"100.25",
               "q":"0.1","m":False}
        await publisher.publish_raw_confirmed(
            "md.aggTrade",frame=frame,
            source="verified_closed_session_replay",
            verified_session_id="rec_fixture01",
            verified_source_record_id="rec_fixture01:aggTrade:101",
            verified_source_ordinal=1,
            verified_source_available_at_ms=1_780_000_000_020,
        )
        await bus.wait_verified_idle()
        assert len(derived)==2
        for event in derived:
            assert event.payload["verified_source_ordinal"]==1
            assert event.payload["verified_session_id"]=="rec_fixture01"
            assert event.payload["verified_source_record_id"]=="rec_fixture01:aggTrade:101"
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_partial_verified_metadata_poisoned_and_reported(tmp_path):
    from core.event_bus import EventBus,VerifiedRawDeliveryError
    from infrastructure.preprocessing.pipeline import MarketDataPipeline

    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only()
    MarketDataPipeline(bus).register()
    await bus.start()
    try:
        frame={"e":"aggTrade","s":"BTCUSDT","a":500,"E":1_780_000_000_010,
               "T":1_780_000_000_000,"f":500,"l":500,"p":"100.25",
               "q":"0.1","m":False}
        with pytest.raises(VerifiedRawDeliveryError,match="failed"):
            await pub.publish_raw_confirmed(
                "md.aggTrade",frame=frame,
                verified_session_id="rec_fixture01",
            )
    finally:
        await bus.stop()


@pytest.mark.asyncio
async def test_historical_volume_delta_uses_receipt_time_not_replay_speed():
    import asyncio
    from core.event_bus import EventBus
    from infrastructure.preprocessing.pipeline import MarketDataPipeline

    async def run_replay(delay: float):
        bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
        pub=bus.activate_verified_market_only()
        MarketDataPipeline(bus,volume_window_s=0.02).register()
        values=[]
        async def receive(event):
            values.append((event.payload["trade_count"],
                           event.payload["volume_delta"]))
        bus.subscribe("indicator.volume_delta",receive)
        await bus.start()
        try:
            for ordinal in (1,2):
                if ordinal == 2:
                    await asyncio.sleep(delay)
                await pub.publish_raw_confirmed(
                    "md.aggTrade",
                    frame={
                        "e":"aggTrade","s":"BTCUSDT","a":500+ordinal,
                        "E":1_780_000_000_000+ordinal,
                        "T":1_780_000_000_000+ordinal,
                        "f":500+ordinal,"l":500+ordinal,
                        "p":"101.25","q":"0.2","m":False,
                    },
                    source="verified_closed_session_replay",
                    verified_session_id="rec_fixture01",
                    verified_source_record_id=f"rec_fixture01:aggTrade:{100+ordinal}",
                    verified_source_ordinal=ordinal,
                    verified_source_available_at_ms=1_780_000_000_010+ordinal,
                )
            await bus.wait_verified_idle()
            return values
        finally:
            await bus.stop()

    fast=await run_replay(0)
    delayed=await run_replay(0.05)
    assert fast == delayed
    assert [v[0] for v in fast] == [1,2]


@pytest.mark.asyncio
async def test_research_session_reset_does_not_mix_unknown_gap_windows():
    import asyncio
    from core.event_bus import EventBus
    from infrastructure.preprocessing.pipeline import MarketDataPipeline

    bus=EventBus(mode_provider=lambda:QuantMode.SIMULATION)
    pub=bus.activate_verified_market_only()
    pipeline=MarketDataPipeline(bus,volume_window_s=10)
    pipeline.register()
    counts=[]
    async def recv(event):
        counts.append(event.payload["trade_count"])
    bus.subscribe("indicator.volume_delta",recv)
    await bus.start()
    try:
        for session_idx in (1,2):
            if session_idx == 2:
                await bus.wait_verified_idle()
                pipeline.reset_verified_research_session()
            sid=f"rec_fixture0{session_idx}"
            await pub.publish_raw_confirmed(
                "md.aggTrade",
                frame={"e":"aggTrade","s":"BTCUSDT","a":500,
                    "E":1_780_000_000_000,"T":1_780_000_000_000,
                    "f":500,"l":500,"p":"100.25","q":"0.1","m":False},
                verified_session_id=sid,
                verified_source_record_id=f"{sid}:aggTrade:101",
                verified_source_ordinal=1,
                verified_source_available_at_ms=1_780_000_000_020,
            )
        await bus.wait_verified_idle()
        assert counts == [1,1]
    finally:
        await bus.stop()


def test_reused_connection_keeps_each_event_durable_to_independent_reader(tmp_path):
    d=OfflineDerivedResearchReceiptLedger(
        tmp_path/"reused.sqlite",
        mode_provider=lambda:QuantMode.SIMULATION,
        reuse_connection=True,
    )
    a=accept(d,1)
    with sqlite3.connect(d.path.resolve().as_uri()+"?mode=ro",uri=True) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM offline_research_derived_receipts"
        ).fetchone()[0]==1
    b=accept(d,2)
    assert not a.replayed and not b.replayed
    with pytest.raises(DerivedResearchReceiptError,match="injected crash"):
        accept(d,3,crash=True)
    with sqlite3.connect(d.path.resolve().as_uri()+"?mode=ro",uri=True) as independent:
        assert independent.execute(
            "SELECT COUNT(*) FROM offline_research_derived_receipts"
        ).fetchone()[0]==2
        assert independent.execute(
            "SELECT last_ordinal FROM offline_research_derived_cursors"
        ).fetchone()[0]==2
    d.close()
    assert OfflineDerivedResearchReceiptLedger(
        d.path,mode_provider=lambda:QuantMode.SIMULATION,
    ).verified_count("rec_fixture01","md.ticker")==2


def test_reused_connection_conflicting_ack_does_not_mutate_cursor(tmp_path):
    d=OfflineDerivedResearchReceiptLedger(
        tmp_path/"bounded_reuse.sqlite",
        mode_provider=lambda:QuantMode.SIMULATION,reuse_connection=True,
    )
    accept(d)
    with pytest.raises(DerivedResearchReceiptError,match="conflicting"):
        accept(d,value=9000)
    assert d.verified_count("rec_fixture01","md.ticker")==1
    d.close()
    assert ledger(tmp_path).verified_count("rec_fixture01","md.ticker")==0


def test_hard_exit_after_reused_connection_full_commit_preserves_receipt(tmp_path):
    import subprocess,sys
    path=tmp_path/"hard_exit_reuse.sqlite"
    code="""
import os,sys
from core.mode import QuantMode
from infrastructure.market_derived_research_receipts import OfflineDerivedResearchReceiptLedger
d=OfflineDerivedResearchReceiptLedger(
    sys.argv[1],mode_provider=lambda:QuantMode.SIMULATION,reuse_connection=True)
d.accept(topic="md.ticker",session_id="rec_fixture01",ordinal=1,
    source_record_id="rec_fixture01:aggTrade:101",
    source_available_at_ms=1780000000001,projection={"symbol":"BTCUSDT","price":101.25})
os._exit(49)
"""
    result=subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==49,result.stderr
    reopened=OfflineDerivedResearchReceiptLedger(
        path,mode_provider=lambda:QuantMode.SIMULATION)
    assert reopened.verified_count("rec_fixture01","md.ticker")==1
    assert accept(reopened).replayed
