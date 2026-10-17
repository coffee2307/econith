"""Source-event idempotency: offline, explicitly opt-in and SIMULATION-only."""
from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ai.inference.predictor import Predictor
from core.event_bus import EventBus
from core.mode import QuantMode
from econith_quant.bridge.ai_bridge import AIBridge
from quant.offline_ack_bridge import OfflineSignalAckBridge
from quant.offline_admission import OfflineAdmissionLedger
from quant.offline_signal_outbox import OfflineSignalOutbox, SourceEventConflict


MARKET_EVENT = {
    "venue": "simulated-feed",
    "stream": "BTCUSDT/trades",
    "exchange_sequence": 8123,
    "exchange_timestamp_ms": 1760716800123,
    "price": "100.00",
}
SOURCE_ID = "simulated-feed:BTCUSDT:trades:8123"


class CapturingBus:
    def __init__(self):
        self.events = []

    async def publish(self, topic, **payload):
        self.events.append((topic, payload))


def make_predictor(bus, box, mode=QuantMode.SIMULATION):
    obj = Predictor.__new__(Predictor)
    obj._bus = bus
    obj._offline_signal_outbox = box
    obj._mode = SimpleNamespace(mode=mode)
    return obj


def test_source_event_deduplicates_across_restart_and_changed_decision(tmp_path):
    db = tmp_path / "source.sqlite"
    box = OfflineSignalOutbox(db, namespace="research")
    original = box.stage_for_event(
        SOURCE_ID, MARKET_EVENT, {"action": "LONG", "direction": 0.8},
        mode=QuantMode.SIMULATION,
    )
    restarted = OfflineSignalOutbox(db, namespace="research")
    retry = restarted.stage_for_event(
        SOURCE_ID, dict(MARKET_EVENT),
        {"action": "SHORT", "direction": -0.9},
        mode=QuantMode.SIMULATION,
    )
    assert original == retry
    assert original.signal_id == "sig:research:1"
    assert len(restarted.pending()) == 1


def test_reused_source_identity_with_different_market_payload_fails_closed(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "source.sqlite")
    first = box.stage_for_event(
        SOURCE_ID, MARKET_EVENT, {"action": "LONG"}, mode=QuantMode.SIMULATION
    )
    conflict = {**MARKET_EVENT, "price": "101.00"}
    with pytest.raises(SourceEventConflict, match="different market event payload"):
        box.stage_for_event(
            SOURCE_ID, conflict, {"action": "SHORT"}, mode=QuantMode.SIMULATION
        )
    with pytest.raises(SourceEventConflict):
        box.get_for_event(SOURCE_ID, conflict)
    assert box.get_for_event(SOURCE_ID, MARKET_EVENT).signal_id == first.signal_id
    assert len(box.pending()) == 1


def test_parallel_first_writers_assign_one_durable_signal(tmp_path):
    db = tmp_path / "concurrent.sqlite"
    box = OfflineSignalOutbox(db, namespace="research")

    def write(index):
        return box.stage_for_event(
            SOURCE_ID, MARKET_EVENT, {"action": "LONG" if index % 2 else "SHORT"},
            mode=QuantMode.SIMULATION,
        ).signal_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(write, range(24)))
    assert set(ids) == {"sig:research:1"}
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM offline_input_decisions").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM offline_signal_outbox").fetchone()[0] == 1


def test_unique_input_events_even_with_same_payload_produce_separate_decisions(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "source.sqlite")
    a = box.stage_for_event(
        SOURCE_ID, MARKET_EVENT, {"action": "FLAT"}, mode=QuantMode.SIMULATION
    )
    b = box.stage_for_event(
        SOURCE_ID + ":next", MARKET_EVENT, {"action": "FLAT"},
        mode=QuantMode.SIMULATION,
    )
    assert a.signal_id != b.signal_id


def test_source_decision_transaction_rolls_back_on_input_mapping_write_fault(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "source.sqlite")
    with sqlite3.connect(box.path) as conn:
        conn.execute("""
            CREATE TRIGGER deny_input BEFORE INSERT ON offline_input_decisions
            BEGIN SELECT RAISE(ABORT, 'injected mapping write fault'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="injected"):
        box.stage_for_event(
            SOURCE_ID, MARKET_EVENT, {"action": "LONG"},
            mode=QuantMode.SIMULATION,
        )
    with sqlite3.connect(box.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM offline_signal_outbox").fetchone()[0] == 0
    assert box.get_for_event(SOURCE_ID, MARKET_EVENT) is None


def test_event_identity_validation_and_reality_block(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "source.sqlite")
    for source_id, source in (
        ("", MARKET_EVENT), ("contains space", MARKET_EVENT),
        (SOURCE_ID, {}), (SOURCE_ID, {"price": float("nan")}),
        (SOURCE_ID, {"invalid": object()}),
    ):
        with pytest.raises(ValueError):
            box.stage_for_event(
                source_id, source, {"action": "LONG"},
                mode=QuantMode.SIMULATION,
            )
    with pytest.raises(RuntimeError, match="SIMULATION"):
        box.stage_for_event(
            SOURCE_ID, MARKET_EVENT, {"action": "LONG"},
            mode=QuantMode.REALITY,
        )
    assert box.pending() == []


def test_predictor_factory_skipped_for_original_event_and_ack_retired(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "source.sqlite")
    bus = CapturingBus()
    predictor = make_predictor(bus, box)
    calls = []

    def factory():
        calls.append("inference")
        return {"action": "LONG", "direction": 0.8}

    initial = asyncio.run(predictor.publish_for_market_event(
        SOURCE_ID, MARKET_EVENT, factory,
    ))
    restarted = make_predictor(bus, OfflineSignalOutbox(box.path))
    repeated = asyncio.run(restarted.publish_for_market_event(
        SOURCE_ID, MARKET_EVENT, lambda: pytest.fail("factory reran on known event"),
    ))
    assert repeated.signal_id == initial.signal_id
    assert calls == ["inference"]
    assert len(bus.events) == 2
    box.acknowledge(initial.signal_id)
    finished = asyncio.run(restarted.publish_for_market_event(
        SOURCE_ID, MARKET_EVENT, lambda: pytest.fail("factory reran after ACK"),
    ))
    assert finished.status == "ACKED"
    assert len(bus.events) == 2


def test_predictor_reality_and_nonoptin_fail_without_evaluating_factory(tmp_path):
    box = OfflineSignalOutbox(tmp_path / "source.sqlite")
    unavailable = make_predictor(CapturingBus(), None)
    real = make_predictor(CapturingBus(), box, QuantMode.REALITY)

    for predictor in (unavailable, real):
        with pytest.raises(RuntimeError):
            asyncio.run(predictor.publish_for_market_event(
                SOURCE_ID, MARKET_EVENT,
                lambda: pytest.fail("factory ran outside allowed sandbox"),
            ))
    assert box.pending() == []


def test_end_to_end_market_source_to_ack_reuses_one_signal_and_admission(tmp_path):
    bus = EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    box = OfflineSignalOutbox(tmp_path / "source.sqlite", namespace="research")
    ledger = OfflineAdmissionLedger(tmp_path / "admission.sqlite")
    predictor = make_predictor(bus, box)
    router = AIBridge(
        bus, offline_signal_outbox=box,
        simulation_mode_provider=lambda: QuantMode.SIMULATION,
    )
    router._mode = "NORMAL"
    router._min_delta = 0.001
    router.register()
    ack = OfflineSignalAckBridge(
        bus, box, ledger, mode_provider=lambda: QuantMode.SIMULATION,
    )
    ack.register()

    async def exercise():
        await bus.start()
        try:
            signal = await predictor.publish_for_market_event(
                SOURCE_ID, MARKET_EVENT,
                lambda: {
                    "action": "LONG", "direction": 0.8,
                    "confidence": 0.9, "regime": "TREND",
                },
            )
            for _ in range(400):
                if box.get(signal.signal_id).status == "ACKED":
                    break
                await asyncio.sleep(0.01)
            assert box.get(signal.signal_id).status == "ACKED"
            replay = await predictor.publish_for_market_event(
                SOURCE_ID, MARKET_EVENT,
                lambda: pytest.fail("no redundant inference after durable ACK"),
            )
            assert replay.signal_id == signal.signal_id
        finally:
            await bus.stop()

    asyncio.run(exercise())
    assert box.pending() == []
    assert not ack.errors
    with sqlite3.connect(ledger.path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM offline_intent_admission").fetchone()[0]
        committed = conn.execute(
            "SELECT COUNT(*) FROM offline_signal_receipts WHERE status='COMMITTED'"
        ).fetchone()[0]
    assert rows >= 1 and committed == 1
