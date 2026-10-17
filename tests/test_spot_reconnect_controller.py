"""Durable multi-session Spot reconnect tests; injected transports only."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportError, SpotTransportResult,
)
from infrastructure.spot_reconnect_controller import (
    OfflineSpotReconnectController, ReconnectError,
)

BASE = 1_780_000_000_000


def step_clock():
    state = [BASE]
    def next_ms():
        state[0] += 10
        return state[0]
    return next_ms


def depth(update=100):
    return json.dumps({
        "stream": "btcusdt@depth@100ms",
        "data": {
            "e": "depthUpdate", "E": BASE, "s": "BTCUSDT",
            "U": update, "u": update + 2,
            "b": [["100", "3"]], "a": [],
        },
    }).encode()


def snapshot(update=100):
    return json.dumps({
        "lastUpdateId": update,
        "bids": [["100", "2"], ["99", "1"]],
        "asks": [["102", "2"], ["103", "3"]],
    }).encode()


def rig(tmp_path, *, controller_id="btc_reader", mode=None):
    flag = [QuantMode.SIMULATION] if mode is None else mode
    source = OfflineSpotCaptureJournal(
        tmp_path / "spot.sqlite", mode_provider=lambda: flag[0],
    )
    current = step_clock()
    adapter = OptInSpotIngressAdapter(
        source, symbol="BTCUSDT", clock_ms=current,
    )
    controller = OfflineSpotReconnectController(
        source, controller_id=controller_id,
        symbol="BTCUSDT", clock_ms=current,
    )
    return controller, adapter, source, current, flag


async def healthy(adapter, session_id, *, update=100):
    async def ws():
        yield depth(update)
        await asyncio.sleep(0.001)

    async def get_snapshot(symbol, limit):
        assert (symbol, limit) == ("BTCUSDT", 1000)
        await asyncio.sleep(0)
        return snapshot(update)

    return await adapter.capture_open_stream(
        ws(), get_snapshot, session_id=session_id,
    )


@pytest.mark.asyncio
async def test_failed_session_then_new_snapshot_recovers_but_gap_is_unverified(tmp_path):
    controller, adapter, source, _, _ = rig(tmp_path)
    calls = []

    async def capture(session_id, seq):
        calls.append((session_id, seq))
        if seq == 1:
            raise OSError("socket disconnected")
        return await healthy(adapter, session_id, update=200)

    outcome = await controller.run_batch(capture, max_attempts=2)
    assert outcome.status == "RECOVERED"
    assert outcome.sessions_attempted == 2
    assert outcome.history_continuity_verified is False
    assert outcome.latest_book_update_id == 202
    assert len(set(session for session, _ in calls)) == 2
    assert [x.status for x in controller.attempts()] == ["FAILED", "SUCCESS"]
    assert [x.failure_code for x in controller.attempts()] == [
        "OSError", None,
    ]
    assert controller.outages()[0].failed_attempts == 1
    assert controller.outages()[0].restored_at_ms is not None
    assert controller.outages()[0].restored_by_session == calls[1][0]
    assert controller.outages()[0].coverage_verified is False
    assert outcome.open_outages == 0
    assert not source.replay_as_of(calls[1][0], BASE + 200)[0].kind != "depthUpdate"


@pytest.mark.asyncio
async def test_exhausted_budget_keeps_open_outage_across_restarts(tmp_path):
    c, adapter, source, current, mode = rig(tmp_path)
    async def broken(session_id, seq):
        raise ConnectionError("contains_sensitive_query=anything")

    result = await c.run_batch(broken, max_attempts=3)
    assert result.status == "EXHAUSTED"
    assert result.open_outages == 1
    assert [x.sequence for x in c.attempts()] == [1, 2, 3]
    assert c.outages()[0].failed_attempts == 3
    assert c.outages()[0].last_failure_code == "ConnectionError"
    assert "anything" not in repr(c.outages())
    with sqlite3.connect(source.path) as db:
        rows = db.execute(
            "SELECT session_id FROM offline_spot_reconnect_attempts"
        ).fetchall()
        assert len({x[0] for x in rows}) == 3
    reopened = OfflineSpotReconnectController(
        OfflineSpotCaptureJournal(
            source.path, mode_provider=lambda: mode[0],
        ),
        controller_id="btc_reader", symbol="BTCUSDT", clock_ms=current,
    )
    output = await reopened.run_batch(broken, max_attempts=1)
    assert output.status == "EXHAUSTED"
    assert len(reopened.attempts()) == 4
    assert reopened.outages()[0].failed_attempts == 4
    assert reopened.outages()[0].restored_at_ms is None


def test_interrupted_attempt_is_reconciled_only_after_explicit_crash_review(tmp_path):
    c, _, source, current, mode = rig(tmp_path)
    abandoned = c.begin_attempt()
    assert abandoned.status == "STARTED"
    after_restart = OfflineSpotReconnectController(
        OfflineSpotCaptureJournal(
            source.path, mode_provider=lambda: mode[0],
        ),
        controller_id="btc_reader", symbol="BTCUSDT", clock_ms=current,
    )
    with pytest.raises(ReconnectError, match="unreconciled"):
        after_restart.begin_attempt()
    assert after_restart.reconcile_crashed_attempts() == 1
    assert after_restart.reconcile_crashed_attempts() == 0
    assert after_restart.attempts()[0].status == "INTERRUPTED"
    assert after_restart.attempts()[0].failure_code == "PROCESS_INTERRUPTED"
    assert after_restart.outages()[0].failed_attempts == 1
    new = after_restart.begin_attempt()
    assert new.sequence == 2 and new.session_id != abandoned.session_id


@pytest.mark.asyncio
async def test_batch_never_auto_steals_other_workers_active_attempt(tmp_path):
    c, _, _, _, _ = rig(tmp_path)
    c.begin_attempt()
    calls = []
    async def would_run(*args):
        calls.append(args)
        raise RuntimeError("should not run")
    with pytest.raises(ReconnectError, match="unreconciled"):
        await c.run_batch(would_run, max_attempts=1)
    assert calls == []
    assert len(c.attempts()) == 1 and c.attempts()[0].status == "STARTED"


@pytest.mark.asyncio
async def test_duplicate_controllers_cannot_claim_same_attempt_simultaneously(tmp_path):
    c1, adapter, source, current, _ = rig(tmp_path)
    c2 = OfflineSpotReconnectController(
        source, controller_id="btc_reader",
        symbol="BTCUSDT", clock_ms=current,
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holding(session_id, seq):
        entered.set()
        await release.wait()
        return await healthy(adapter, session_id)

    worker1 = asyncio.create_task(c1.run_batch(holding, max_attempts=1))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        with pytest.raises(ReconnectError, match="unreconciled"):
            await c2.run_batch(holding, max_attempts=1)
    finally:
        release.set()
    assert (await worker1).status == "RECOVERED"
    assert len(c1.attempts()) == 1


@pytest.mark.asyncio
async def test_retry_backoff_is_bounded_and_recorded_only_as_attempts(tmp_path):
    c, _, _, _, _ = rig(tmp_path)
    delays = []

    async def sleep(value):
        delays.append(value)

    async def broken(*args):
        raise TimeoutError("testing timeout")

    result = await c.run_batch(broken, max_attempts=3, backoff_s=2, sleep=sleep)
    assert result.status == "EXHAUSTED"
    assert delays == [2, 4]
    assert [r.status for r in c.attempts()] == ["FAILED"] * 3


@pytest.mark.asyncio
async def test_forged_success_does_not_close_outage_without_immutable_source(tmp_path):
    c, _, _, _, _ = rig(tmp_path)
    async def forged(session_id, seq):
        return SpotTransportResult(
            session_id, "BTCUSDT", 1, 1, BASE + 300, 1,
            "SYNCED", 102,
        )
    result = await c.run_batch(forged, max_attempts=1)
    assert result.status == "EXHAUSTED"
    assert c.attempts()[0].status == "FAILED"
    assert c.outages()[0].restored_at_ms is None


@pytest.mark.asyncio
async def test_failure_receipt_transaction_rolls_back_on_injected_disk_error(tmp_path):
    c, _, journal, _, _ = rig(tmp_path)
    with sqlite3.connect(journal.path) as db:
        db.execute("""
            CREATE TRIGGER reject_outage BEFORE INSERT
            ON offline_spot_reconnect_outages
            BEGIN SELECT RAISE(ABORT, 'full disk outage'); END
        """)
    async def fail(*args):
        raise OSError("network gone")
    with pytest.raises(sqlite3.DatabaseError, match="full disk outage"):
        await c.run_batch(fail, max_attempts=1)
    # Atomic: no FAILED attempt without durable outage, and no false SUCCESS.
    assert [a.status for a in c.attempts()] == ["STARTED"]
    assert c.outages() == []
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TRIGGER reject_outage")
    assert c.reconcile_crashed_attempts() == 1
    assert c.outages()[0].last_failure_code == "PROCESS_INTERRUPTED"


@pytest.mark.asyncio
async def test_cancel_records_interrupted_and_never_returns_success(tmp_path):
    c, _, _, _, _ = rig(tmp_path)
    entered = asyncio.Event()
    async def blocked(*args):
        entered.set()
        await asyncio.sleep(500)
    job = asyncio.create_task(c.run_batch(blocked, max_attempts=1))
    await asyncio.wait_for(entered.wait(), 1)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert c.attempts()[0].status == "INTERRUPTED"
    assert c.outages()[0].restored_at_ms is None


@pytest.mark.asyncio
async def test_mode_switch_refuses_to_mark_success_and_remains_recoverable(tmp_path):
    mode = [QuantMode.SIMULATION]
    c, _, _, _, _ = rig(tmp_path, mode=mode)
    async def turning(*args):
        mode[0] = QuantMode.REALITY
        raise OSError("upstream")
    with pytest.raises(RuntimeError, match="SIMULATION"):
        await c.run_batch(turning, max_attempts=1)
    mode[0] = QuantMode.SIMULATION
    assert c.attempts()[0].status == "STARTED"
    assert c.reconcile_crashed_attempts() == 1
    assert c.outages()[0].coverage_verified is False


@pytest.mark.asyncio
async def test_public_capture_needs_explicit_allow_network_and_matching_adapter(tmp_path):
    c, adapter, source, _, _ = rig(tmp_path)
    with pytest.raises(PermissionError, match="explicit"):
        await c.run_public(adapter)
    another = OptInSpotIngressAdapter(
        OfflineSpotCaptureJournal(
            tmp_path / "another.db",
            mode_provider=lambda: QuantMode.SIMULATION,
        ),
        symbol="BTCUSDT",
    )
    with pytest.raises(ValueError, match="share controller journal"):
        await c.run_public(another, allow_network=True)
    assert c.attempts() == []


@pytest.mark.parametrize("kwargs", [
    {"max_attempts": 0},
    {"max_attempts": 11},
    {"max_attempts": True},
    {"backoff_s": -1},
    {"backoff_s": float("nan")},
    {"backoff_s": 31},
])
@pytest.mark.asyncio
async def test_bounded_retry_arguments_fail_before_any_attempt(tmp_path, kwargs):
    c, _, _, _, _ = rig(tmp_path)
    async def unused(*args):
        raise RuntimeError("should not run")
    with pytest.raises(ValueError):
        await c.run_batch(unused, **kwargs)
    assert c.attempts() == []


def test_separate_controller_names_do_not_merge_incident_histories(tmp_path):
    first, _, source, current, _ = rig(tmp_path, controller_id="first")
    second = OfflineSpotReconnectController(
        source, controller_id="second", symbol="BTCUSDT", clock_ms=current,
    )
    first.begin_attempt()
    assert len(first.attempts()) == 1
    assert second.attempts() == []
    assert second.outages() == []
    assert second.reconcile_crashed_attempts() == 0


def test_ledger_status_reads_forbidden_in_reality(tmp_path):
    c, _, _, _, mode = rig(tmp_path)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        c.begin_attempt()
    with pytest.raises(RuntimeError, match="SIMULATION"):
        c.reconcile_crashed_attempts()
    with pytest.raises(RuntimeError, match="SIMULATION"):
        c.attempts()
    with pytest.raises(RuntimeError, match="SIMULATION"):
        c.outages()


def test_reused_controller_id_cannot_mix_distinct_spot_symbols(tmp_path):
    c, _, journal, current, _ = rig(tmp_path)
    with pytest.raises(ReconnectError, match="another symbol"):
        OfflineSpotReconnectController(
            journal, controller_id="btc_reader",
            symbol="ETHUSDT", clock_ms=current,
        )
    assert c.attempts() == []


@pytest.mark.asyncio
async def test_stale_closed_session_result_cannot_be_reused_for_next_attempt(tmp_path):
    c, adapter, _, _, _ = rig(tmp_path)
    past = await healthy(adapter, "unrelated_session")
    async def replay_old_result(session_id, seq):
        assert session_id != past.session_id
        return past
    result = await c.run_batch(replay_old_result, max_attempts=1)
    assert result.status == "EXHAUSTED"
    assert c.attempts()[0].status == "FAILED"
    assert c.outages()[0].coverage_verified is False


@pytest.mark.asyncio
async def test_each_successful_finite_session_is_independent_not_continuous(tmp_path):
    c, adapter, _, _, _ = rig(tmp_path)
    async def capture(session_id, seq):
        return await healthy(adapter, session_id, update=100 + seq * 100)
    first = await c.run_batch(capture, max_attempts=1)
    second = await c.run_batch(capture, max_attempts=1)
    assert first.status == second.status == "RECOVERED"
    assert first.last_session_id != second.last_session_id
    assert [a.sequence for a in c.attempts()] == [1, 2]
    assert second.history_continuity_verified is False
