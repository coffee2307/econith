"""SIMULATION-only all-leg durable ACK contract, including crash windows."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from core.event_bus import EventBus
from core.mode import QuantMode
from quant.offline_ack_bridge import (
    AdmissionReceiptError,
    OfflineSignalAckBridge,
)
from quant.offline_admission import (
    IntentConflict,
    OfflineAdmissionLedger,
    fingerprint,
)
from quant.offline_signal_outbox import (
    OfflineSignalOutbox,
    derive_leg_intent_id,
)


def setup_research(tmp_path, *, bus=None, mode=None):
    bus = bus or EventBus(mode_provider=lambda: QuantMode.SIMULATION)
    outbox = OfflineSignalOutbox(tmp_path / "signal-outbox.sqlite", namespace="experiment")
    ledger = OfflineAdmissionLedger(tmp_path / "intent-inbox.sqlite")
    bridge = OfflineSignalAckBridge(
        bus, outbox, ledger,
        mode_provider=lambda: QuantMode.SIMULATION if mode is None else mode[0],
    )
    return bus, outbox, ledger, bridge


def stage_signal(outbox):
    return outbox.stage({
        "action": "LONG", "direction": 0.8, "confidence": 0.9,
        "regime": "TREND",
    })


def leg(signal, index=0, count=1, symbol="BTCUSDT"):
    return {
        "signal_id": signal.signal_id,
        "signal_fingerprint": fingerprint(signal.payload),
        "intent_id": derive_leg_intent_id(signal.signal_id, index, symbol),
        "leg_index": index,
        "leg_count": count,
        "symbol": symbol,
        "side": "BUY",
        "quantity": 0.01,
        "reduce_only": False,
        "reason": "simulation-only-test",
    }


def test_eventbus_full_predictor_outbox_aibridge_inbox_ack(tmp_path):
    """The real AIBridge router must emit every leg before an outbox ACK."""
    from econith_quant.bridge.ai_bridge import AIBridge

    bus, outbox, ledger, inbox = setup_research(tmp_path)
    producer = AIBridge(bus)
    producer._mode = "NORMAL"
    producer._min_delta = 0.001
    producer.register()
    inbox.register()
    source = stage_signal(outbox)

    async def scenario():
        await bus.start()
        try:
            await outbox.publish_to_simulation(
                bus, source, mode=QuantMode.SIMULATION
            )
            for _ in range(250):
                if outbox.get(source.signal_id).status == "ACKED":
                    return
                await asyncio.sleep(0.01)
        finally:
            await bus.stop()
        raise AssertionError("all-leg receipt ACK was not observed")

    asyncio.run(scenario())
    assert outbox.pending() == []
    with sqlite3.connect(ledger.path) as db:
        receipt = db.execute(
            "SELECT status,expected_legs FROM offline_signal_receipts WHERE signal_id=?",
            (source.signal_id,),
        ).fetchone()
        assert receipt and receipt[0] == "COMMITTED"
        legs = db.execute(
            "SELECT COUNT(*) FROM offline_signal_receipt_legs WHERE signal_id=?",
            (source.signal_id,),
        ).fetchone()[0]
        admissions = db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission",
        ).fetchone()[0]
    assert legs == admissions == receipt[1] >= 1


def test_two_legs_are_atomic_per_leg_and_ack_only_after_complete(tmp_path):
    _, outbox, ledger, inbox = setup_research(tmp_path)
    source = stage_signal(outbox)
    outbox.mark_queued(source.signal_id)
    first = leg(source, 0, 2)
    second = leg(source, 1, 2, "ETHUSDT")

    assert inbox.admit_leg(first) is False
    assert not inbox.is_complete(source.signal_id, first["signal_fingerprint"], 2)
    assert not inbox.acknowledge_if_committed(
        source.signal_id, first["signal_fingerprint"], 2
    )
    assert outbox.get(source.signal_id).status == "QUEUED"
    assert inbox.admit_leg(second) is True
    assert inbox.is_complete(source.signal_id, first["signal_fingerprint"], 2)
    assert inbox.acknowledge_if_committed(
        source.signal_id, first["signal_fingerprint"], 2
    )
    assert outbox.get(source.signal_id).status == "ACKED"
    assert inbox.acknowledge_if_committed(
        source.signal_id, first["signal_fingerprint"], 2
    )  # duplicate ACK, no duplicated records
    assert inbox.admit_leg(first) is True  # late same payload replay still safe
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] == 2


def test_crash_after_receipt_before_ack_can_recover_without_order_replay(tmp_path):
    bus, outbox, ledger, inbox = setup_research(tmp_path)
    source = stage_signal(outbox)
    outbox.mark_queued(source.signal_id)
    first = leg(source)
    assert inbox.admit_leg(first)
    assert outbox.get(source.signal_id).status == "QUEUED"

    # Rebuild the process-local EventBus and consumer from the persisted files.
    bus2, outbox2, ledger2, restarted = setup_research(tmp_path)
    restarted.register()

    async def recover():
        await bus2.start()
        try:
            assert await restarted.replay_verified_acks() == 1
            for _ in range(120):
                if outbox2.get(source.signal_id).status == "ACKED":
                    return
                await asyncio.sleep(0.01)
        finally:
            await bus2.stop()
        raise AssertionError("receipt re-ack failed after simulated crash")

    asyncio.run(recover())
    with sqlite3.connect(ledger2.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] == 1
    assert outbox2.pending() == []


def test_forged_ack_cannot_retire_unadmitted_signal(tmp_path):
    bus, outbox, _, inbox = setup_research(tmp_path)
    source = stage_signal(outbox)
    outbox.mark_queued(source.signal_id)
    inbox.register()

    async def scenario():
        await bus.start()
        try:
            await bus.publish(
                "offline.signal.ack",
                signal_id=source.signal_id,
                signal_fingerprint=fingerprint(source.payload),
                leg_count=1,
            )
            await asyncio.sleep(0.04)
        finally:
            await bus.stop()

    asyncio.run(scenario())
    assert outbox.get(source.signal_id).status == "QUEUED"
    assert not inbox.acknowledge_if_committed(
        source.signal_id, "0" * 64, 1
    )
    assert not inbox.acknowledge_if_committed(
        source.signal_id, fingerprint(source.payload), 3
    )


def test_conflicting_plan_cardinality_and_payload_cannot_commit_receipt(tmp_path):
    _, outbox, ledger, inbox = setup_research(tmp_path)
    signal = stage_signal(outbox)
    outbox.mark_queued(signal.signal_id)
    assert inbox.admit_leg(leg(signal, 0, 2)) is False
    with pytest.raises(AdmissionReceiptError, match="cardinality"):
        inbox.admit_leg(leg(signal, 1, 3, "ETHUSDT"))
    modified = leg(signal, 0, 2)
    modified["quantity"] = 0.02
    with pytest.raises(IntentConflict, match="changed business data"):
        inbox.admit_leg(modified)
    assert not inbox.is_complete(signal.signal_id, fingerprint(signal.payload), 2)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] == 1


def test_incorrect_origin_or_order_ids_are_denied_and_not_persisted(tmp_path):
    _, outbox, ledger, inbox = setup_research(tmp_path)
    source = stage_signal(outbox)
    for changes in (
        {"signal_fingerprint": "0" * 64},
        {"intent_id": "random-venue-id"},
        {"quantity": float("nan")},
        {"quantity": -5.0},
        {"side": "HOLD"},
        {"reduce_only": "yes"},
        {"leg_count": 0},
        {"leg_index": True},
    ):
        value = {**leg(source), **changes}
        with pytest.raises((AdmissionReceiptError, ValueError)):
            inbox.admit_leg(value)
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] == 0


def test_reality_transition_prevents_new_admissions_and_acks(tmp_path):
    mode = [QuantMode.SIMULATION]
    _, outbox, ledger, inbox = setup_research(tmp_path, mode=mode)
    signal = stage_signal(outbox)
    outbox.mark_queued(signal.signal_id)
    mode[0] = QuantMode.REALITY
    with pytest.raises(AdmissionReceiptError, match="SIMULATION-only"):
        inbox.admit_leg(leg(signal))
    with pytest.raises(AdmissionReceiptError, match="SIMULATION-only"):
        inbox.acknowledge_if_committed(
            signal.signal_id, fingerprint(signal.payload), 1
        )
    with pytest.raises(AdmissionReceiptError, match="SIMULATION-only"):
        asyncio.run(inbox.replay_verified_acks())
    assert outbox.get(signal.signal_id).status == "QUEUED"
    assert ledger.get(derive_leg_intent_id(signal.signal_id, 0, "BTCUSDT")) is None


def test_crash_mid_multileg_replay_completes_once(tmp_path):
    _, outbox, ledger, inbox = setup_research(tmp_path)
    signal = stage_signal(outbox)
    outbox.mark_queued(signal.signal_id)
    assert not inbox.admit_leg(leg(signal, 0, 2))
    _, outbox2, ledger2, resumed = setup_research(tmp_path)
    assert not resumed.admit_leg(leg(signal, 0, 2))  # same first leg repeated
    assert resumed.admit_leg(leg(signal, 1, 2, "ETHUSDT"))
    assert resumed.acknowledge_if_committed(
        signal.signal_id, fingerprint(signal.payload), 2
    )
    assert outbox2.get(signal.signal_id).status == "ACKED"
    with sqlite3.connect(ledger2.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] == 2


def test_event_queued_but_bus_not_started_replays_after_restart(tmp_path):
    """EventBus queued the source, but no subscriber ran before process death."""
    from econith_quant.bridge.ai_bridge import AIBridge

    initial_bus, initial_outbox, _, _ = setup_research(tmp_path)
    source = stage_signal(initial_outbox)
    asyncio.run(initial_outbox.publish_to_simulation(
        initial_bus, source, mode=QuantMode.SIMULATION,
    ))
    assert initial_outbox.get(source.signal_id).status == "QUEUED"

    # New process, new queue: only the persistent outbox survived.
    new_bus, new_outbox, new_ledger, new_inbox = setup_research(tmp_path)
    bridge = AIBridge(new_bus)
    bridge._mode = "NORMAL"
    bridge._min_delta = 0.001
    bridge.register()
    new_inbox.register()

    async def recover():
        await new_bus.start()
        try:
            await new_outbox.publish_to_simulation(
                new_bus, new_outbox.pending()[0], mode=QuantMode.SIMULATION,
            )
            for _ in range(250):
                if new_outbox.get(source.signal_id).status == "ACKED":
                    return
                await asyncio.sleep(0.01)
        finally:
            await new_bus.stop()
        raise AssertionError("queued-only signal was not redelivered after restart")

    asyncio.run(recover())
    with sqlite3.connect(new_ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] >= 1


def test_concurrent_valid_receipts_do_not_fail_or_double_admit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _, outbox, ledger, inbox = setup_research(tmp_path)
    signal = stage_signal(outbox)
    outbox.mark_queued(signal.signal_id)
    assert inbox.admit_leg(leg(signal))
    receipt = (signal.signal_id, fingerprint(signal.payload), 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(
            lambda _: inbox.acknowledge_if_committed(*receipt), range(8),
        ))
    assert all(results)
    assert outbox.get(signal.signal_id).status == "ACKED"
    with sqlite3.connect(ledger.path) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_intent_admission"
        ).fetchone()[0] == 1
