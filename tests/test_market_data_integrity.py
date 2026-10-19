"""Phase 1 source integrity, point-in-time admission and crash recovery."""
from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ai.inference.predictor import Predictor
from core.mode import QuantMode
from infrastructure.market_data_integrity import (
    MarketEventError, OfflineMarketIntegrityJournal,
)
from quant.offline_signal_outbox import OfflineSignalOutbox

BASE = 1_780_000_000_000


def trade(sequence=100, *, event_ms=BASE, price="100.00", symbol="BTCUSDT"):
    return {
        "e": "aggTrade", "E": event_ms, "T": event_ms - 10,
        "s": symbol, "a": sequence, "f": sequence,
        "l": sequence, "p": price, "q": "0.015", "m": False,
    }


def journal(tmp_path, *, market="binance_spot", mode=None, **kwargs):
    active = mode if mode is not None else [QuantMode.SIMULATION]
    return OfflineMarketIntegrityJournal(
        tmp_path / "market.sqlite", market=market,
        mode_provider=lambda: active[0], **kwargs,
    )


def ingest(j, frame, *, received=BASE + 100, source="binance_public_simulation"):
    return j.ingest_agg_trade(frame, source=source, received_at_ms=received)


def test_first_baseline_and_contiguous_trade_ids_are_accepted(tmp_path):
    j = journal(tmp_path)
    one = ingest(j, trade(100))
    two = ingest(j, trade(101), received=BASE + 200)
    assert one.status == two.status == "ACCEPTED"
    assert one.event_id == "md:binance_spot:binance_public_simulation:BTCUSDT:aggTrade:100"
    assert [r.sequence for r in j.replay_as_of(BASE + 300)] == [100, 101]
    assert j.unresolved_incidents() == []


def test_duplicate_retries_retain_original_asof_without_second_row(tmp_path):
    j = journal(tmp_path)
    frame = trade(100)
    first = ingest(j, frame)
    replay = ingest(j, frame, received=BASE + 500_000)
    assert replay.status == "DUPLICATE"
    assert first.available_at_ms == replay.available_at_ms == BASE + 100
    assert len(j.replay_as_of(BASE + 100)) == 1
    assert j.unresolved_incidents() == []


def test_conflicting_event_id_does_not_mutate_canonical_source(tmp_path):
    j = journal(tmp_path)
    first = ingest(j, trade(100))
    altered = ingest(j, trade(100, price="99.9"))
    assert altered.status == "CONFLICT"
    assert [x.frame["p"] for x in j.replay_as_of(BASE + 100)] == ["100.00"]
    assert [x["status"] for x in j.unresolved_incidents()] == ["CONFLICT"]
    assert first.event_id == altered.event_id


def test_sequence_gap_is_quarantined_and_requires_missing_frames(tmp_path):
    j = journal(tmp_path)
    assert ingest(j, trade(100)).accepted
    assert ingest(j, trade(103)).status == "GAP"
    assert ingest(j, trade(103), received=BASE + 200).status == "GAP"
    assert [x.sequence for x in j.replay_as_of(BASE + 400)] == [100]
    assert ingest(j, trade(101), received=BASE + 250).accepted
    assert ingest(j, trade(102), received=BASE + 260).accepted
    assert ingest(j, trade(103), received=BASE + 270).accepted
    assert [x.sequence for x in j.replay_as_of(BASE + 400)] == [100, 101, 102, 103]
    assert [x["status"] for x in j.unresolved_incidents()] == ["GAP", "GAP"]


def test_unseen_old_sequence_is_out_of_order_but_different_scope_is_independent(tmp_path):
    j = journal(tmp_path)
    ingest(j, trade(100))
    assert ingest(j, trade(99)).status == "OUT_OF_ORDER"
    assert ingest(j, trade(99, symbol="ETHUSDT")).accepted
    assert ingest(j, trade(99), source="synthetic_mock").accepted
    assert [x["status"] for x in j.unresolved_incidents()] == ["OUT_OF_ORDER"]
    assert len(j.replay_as_of(BASE + 100)) == 3


def test_event_received_late_is_never_retroactively_visible(tmp_path):
    j = journal(tmp_path, max_age_ms=10000)
    assert ingest(j, trade(100), received=BASE + 200).accepted
    assert ingest(j, trade(101, event_ms=BASE + 210), received=BASE + 300).accepted
    assert [x.sequence for x in j.replay_as_of(BASE + 199)] == []
    assert [x.sequence for x in j.replay_as_of(BASE + 250)] == [100]
    assert [x.sequence for x in j.replay_as_of(BASE + 300)] == [100, 101]


def test_stale_future_and_trade_clock_lag_quarantined(tmp_path):
    j = journal(tmp_path, max_age_ms=100, max_future_skew_ms=20)
    stale = ingest(j, trade(100), received=BASE + 500)
    future = ingest(j, trade(100, event_ms=BASE + 500), received=BASE + 50)
    skewed = trade(100)
    skewed["T"] = BASE - 500
    clock_lag = ingest(j, skewed, received=BASE + 50)
    assert [stale.status, future.status, clock_lag.status] == [
        "STALE", "FUTURE", "STALE",
    ]
    assert j.replay_as_of(BASE + 900) == []
    assert len(j.unresolved_incidents()) == 3


def test_malformed_and_nonfinite_market_inputs_refused(tmp_path):
    j = journal(tmp_path)
    bad = [
        {"lastUpdateId": 99, "bids": [], "asks": []},
        {**trade(), "a": True},
        {**trade(), "s": "btc/usdt"},
        {**trade(), "p": "NaN"},
        {**trade(), "q": "-3"},
        {**trade(), "m": "False"},
        {**trade(), "f": 103, "l": 102},
        {**trade(), "unknown": object()},
    ]
    for data in bad:
        with pytest.raises(MarketEventError):
            ingest(j, data)
    with pytest.raises(MarketEventError):
        ingest(j, trade(), source="invalid/source")
    with pytest.raises(MarketEventError):
        ingest(j, trade(), received=True)
    assert j.replay_as_of(BASE + 100) == []


def test_simulation_only_and_explicit_provider(tmp_path):
    mode = [QuantMode.REALITY]
    j = journal(tmp_path, mode=mode)
    with pytest.raises(RuntimeError, match="SIMULATION-only"):
        ingest(j, trade())
    with pytest.raises(RuntimeError, match="SIMULATION-only"):
        j.replay_as_of(BASE + 100)
    with pytest.raises(ValueError, match="mode provider"):
        OfflineMarketIntegrityJournal(tmp_path / "other.sqlite", mode_provider=None)
    mode[0] = QuantMode.SIMULATION
    assert ingest(j, trade()).accepted
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION-only"):
        j.unresolved_incidents()


def test_reopen_replays_original_receipt_timestamps_and_cursors(tmp_path):
    j = journal(tmp_path)
    first = ingest(j, trade(100), received=BASE + 90)
    restarted = journal(tmp_path)
    assert ingest(restarted, trade(100), received=BASE + 500).status == "DUPLICATE"
    assert ingest(restarted, trade(102)).status == "GAP"
    assert ingest(restarted, trade(101)).accepted
    assert restarted.replay_as_of(BASE + 90)[0].event_id == first.event_id


def test_concurrent_workers_cannot_double_ingest(tmp_path):
    j = journal(tmp_path)
    frame = trade(100)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: ingest(j, frame), range(20)))
    assert [x.status for x in outcomes].count("ACCEPTED") == 1
    assert [x.status for x in outcomes].count("DUPLICATE") == 19
    assert len(j.replay_as_of(BASE + 100)) == 1


def test_atomic_ingest_failure_rolls_back_event_and_cursor(tmp_path):
    j = journal(tmp_path)
    with sqlite3.connect(j.path) as conn:
        conn.execute("""
            CREATE TRIGGER reject_cursor BEFORE INSERT ON offline_market_cursors
            BEGIN SELECT RAISE(ABORT, 'simulated cursor fault'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="cursor fault"):
        ingest(j, trade(100))
    with sqlite3.connect(j.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM offline_market_events").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM offline_market_cursors").fetchone()[0] == 0


def test_integrity_check_detects_modified_record_on_replay(tmp_path):
    j = journal(tmp_path)
    ingest(j, trade(100))
    with sqlite3.connect(j.path) as conn:
        conn.execute(
            "UPDATE offline_market_events SET frame_json=? WHERE sequence=100",
            ('{"e":"aggTrade","a":100}',),
        )
    with pytest.raises(MarketEventError):
        j.replay_as_of(BASE + 100)


def test_different_market_scopes_do_not_share_sequence_cursors(tmp_path):
    spot = journal(tmp_path, market="binance_spot")
    futures = journal(tmp_path, market="binance_futures")
    assert ingest(spot, trade(100)).accepted
    assert ingest(futures, trade(100)).accepted
    assert len(spot.replay_as_of(BASE + 100)) == 2


def test_accepted_record_can_feed_existing_durable_predictor_api(tmp_path):
    j = journal(tmp_path)
    event = ingest(j, trade(100))
    assert event.accepted
    stored = j.replay_as_of(BASE + 100)[0]
    box = OfflineSignalOutbox(tmp_path / "decisions.sqlite")
    class Bus:
        def __init__(self):
            self.events = []
        async def publish(self, topic, **payload):
            self.events.append((topic, payload))
    bus = Bus()
    predictor = Predictor.__new__(Predictor)
    predictor._bus = bus
    predictor._offline_signal_outbox = box
    predictor._mode = SimpleNamespace(mode=QuantMode.SIMULATION)

    async def publish():
        return await predictor.publish_for_market_event(
            stored.event_id, stored.frame, lambda: {"action": "FLAT"}
        )
    first = asyncio.run(publish())
    second = asyncio.run(publish())
    assert first.signal_id == second.signal_id
    assert len(box.pending()) == 1
    assert [name for name, _ in bus.events] == ["ai.signal", "ai.signal"]
