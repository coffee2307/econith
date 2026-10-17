"""Adversarial SQLite fencing and reconnect ownership, no live sockets."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter
from infrastructure.spot_reconnect_controller import (
    OfflineSpotReconnectController, ReconnectError,
)
from infrastructure.spot_worker_fence import (
    OfflineSpotWorkerFence, WorkerLeaseConflict,
)

BASE = 1_780_000_000_000


class Clock:
    def __init__(self):
        self.value = BASE

    def __call__(self):
        self.value += 10
        return self.value

    def jump(self, ms):
        self.value += ms


def rig(tmp_path, *, mode=None, name="btc_fenced", clock=None):
    flag = [QuantMode.SIMULATION] if mode is None else mode
    now = Clock() if clock is None else clock
    journal = OfflineSpotCaptureJournal(
        tmp_path / "raw.sqlite", mode_provider=lambda: flag[0],
    )
    fence = OfflineSpotWorkerFence(
        journal, controller_id=name, clock_ms=now, lease_ms=1000,
    )
    controller = OfflineSpotReconnectController(
        journal, controller_id=name, symbol="BTCUSDT",
        clock_ms=now, worker_fence=fence,
    )
    adapter = OptInSpotIngressAdapter(
        journal, symbol="BTCUSDT", clock_ms=now,
    )
    return controller, fence, adapter, journal, now, flag


async def captured(adapter, session_id):
    message = json.dumps({
        "stream": "btcusdt@depth@100ms",
        "data": {
            "e": "depthUpdate", "s": "BTCUSDT", "E": BASE,
            "U": 100, "u": 102, "b": [["100","3"]], "a": [],
        },
    }).encode()

    async def ws():
        yield message
        await asyncio.sleep(0.002)

    async def rest(symbol, limit):
        return json.dumps({
            "lastUpdateId": 100,
            "bids": [["100","1"]],
            "asks": [["102","3"]],
        }).encode()

    return await adapter.capture_open_stream(
        ws(), rest, session_id=session_id,
    )


def test_lease_acquire_renew_release_and_generation_token(tmp_path):
    _, fence, _, _, clock, _ = rig(tmp_path)
    first = fence.acquire(holder_id="worker_one")
    assert first.generation == 1
    assert fence.status().active
    with pytest.raises(WorkerLeaseConflict, match="already held"):
        fence.acquire(holder_id="worker_two")
    refreshed = fence.renew(first)
    assert refreshed.token == first.token
    assert refreshed.expires_at_ms > first.expires_at_ms
    with pytest.raises(WorkerLeaseConflict, match="superseded"):
        fence.release(first)
    fence.release(refreshed)
    assert not fence.status().active
    second = fence.acquire(holder_id="worker_two")
    assert second.generation == 2
    assert second.token != first.token
    assert second.holder_id == "worker_two"
    with pytest.raises(WorkerLeaseConflict):
        fence.renew(refreshed)


def test_expiry_reissue_fences_all_old_controller_mutations(tmp_path):
    c, fence, _, _, clock, _ = rig(tmp_path)
    old = fence.acquire(holder_id="worker_one")
    started = c.begin_attempt(lease=old)
    clock.jump(2000)
    new = fence.acquire(holder_id="worker_two")
    with pytest.raises(WorkerLeaseConflict):
        c.reconcile_crashed_attempts(lease=old)
    with pytest.raises(WorkerLeaseConflict):
        c.begin_attempt(lease=old)
    with pytest.raises(WorkerLeaseConflict):
        c._complete(started.attempt_id, status="FAILED",
                    failure_code="OSError", lease=old)
    assert c.attempts()[0].status == "STARTED"
    assert c.outages() == []
    assert c.reconcile_crashed_attempts(lease=new) == 1
    assert c.attempts()[0].status == "INTERRUPTED"
    assert c.outages()[0].coverage_verified is False
    new_attempt = c.begin_attempt(lease=new)
    assert new_attempt.session_id != started.session_id


def test_optin_policy_blocks_all_unfenced_mutations_even_from_old_object(tmp_path):
    clock = Clock()
    source = OfflineSpotCaptureJournal(
        tmp_path / "policy.db",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    legacy = OfflineSpotReconnectController(
        source, controller_id="btc_reader", symbol="BTCUSDT",
        clock_ms=clock,
    )
    fence = OfflineSpotWorkerFence(
        source, controller_id="btc_reader", clock_ms=clock, lease_ms=1000,
    )
    guarded = OfflineSpotReconnectController(
        source, controller_id="btc_reader", symbol="BTCUSDT",
        clock_ms=clock, worker_fence=fence,
    )
    with pytest.raises(WorkerLeaseConflict):
        legacy.begin_attempt()
    with pytest.raises(WorkerLeaseConflict):
        legacy.reconcile_crashed_attempts()
    with pytest.raises(ReconnectError, match="requires a durable"):
        OfflineSpotReconnectController(
            source, controller_id="btc_reader",
            symbol="BTCUSDT", clock_ms=clock,
        )
    claim = fence.acquire(holder_id="worker_one")
    assert guarded.begin_attempt(lease=claim).status == "STARTED"


def test_simultaneous_acquire_has_single_winner_sqlite(tmp_path):
    _, fence, _, journal, _, _ = rig(tmp_path)
    competitors = [
        OfflineSpotWorkerFence(
            journal, controller_id="btc_fenced",
            clock_ms=lambda: BASE + 200, lease_ms=1000,
        ) for _ in range(12)
    ]
    def attempt(i):
        try:
            return competitors[i].acquire(holder_id=f"owner_{i}")
        except WorkerLeaseConflict:
            return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        claims = list(pool.map(attempt, range(12)))
    winners = [c for c in claims if c is not None]
    assert len(winners) == 1
    assert fence.status(now_ms=BASE + 200).generation == 1


@pytest.mark.asyncio
async def test_fenced_batch_success_and_clean_release(tmp_path):
    c, fence, adapter, _, _, _ = rig(tmp_path)
    outcome = await c.run_fenced_batch(
        lambda session_id, seq: captured(adapter, session_id),
        holder_id="worker_one", max_attempts=1,
        heartbeat_interval_s=0.02,
    )
    assert outcome.status == "RECOVERED"
    assert outcome.latest_book_update_id == 102
    assert outcome.history_continuity_verified is False
    assert [x.status for x in c.attempts()] == ["SUCCESS"]
    assert c.outages() == []
    assert not fence.status().active


@pytest.mark.asyncio
async def test_exclusive_fenced_run_does_not_let_competitor_reconcile(tmp_path):
    c, fence, adapter, journal, clock, _ = rig(tmp_path)
    other_fence = OfflineSpotWorkerFence(
        journal, controller_id="btc_fenced", clock_ms=clock, lease_ms=1000,
    )
    other = OfflineSpotReconnectController(
        journal, controller_id="btc_fenced",
        symbol="BTCUSDT", clock_ms=clock, worker_fence=other_fence,
    )
    started = asyncio.Event()
    allow = asyncio.Event()

    async def slow(session_id, seq):
        started.set()
        await allow.wait()
        return await captured(adapter, session_id)

    task = asyncio.create_task(c.run_fenced_batch(
        slow, holder_id="primary", max_attempts=1,
        heartbeat_interval_s=0.02,
    ))
    try:
        await asyncio.wait_for(started.wait(), timeout=1.0)
        with pytest.raises(WorkerLeaseConflict, match="already held"):
            await other.run_fenced_batch(
                slow, holder_id="competitor", max_attempts=1,
                heartbeat_interval_s=0.02,
            )
        with pytest.raises(WorkerLeaseConflict):
            other.reconcile_crashed_attempts()
        assert c.attempts()[0].status == "STARTED"
    finally:
        allow.set()
    assert (await task).status == "RECOVERED"
    assert len(c.attempts()) == 1


@pytest.mark.asyncio
async def test_expired_lease_then_fenced_restart_reconciles_stale_attempt(tmp_path):
    c, fence, adapter, _, clock, _ = rig(tmp_path)
    lost = fence.acquire(holder_id="lost_process")
    prev = c.begin_attempt(lease=lost)
    clock.jump(1200)
    outcome = await c.run_fenced_batch(
        lambda sid, seq: captured(adapter, sid),
        holder_id="replacement", max_attempts=1,
        heartbeat_interval_s=0.02,
    )
    assert outcome.status == "RECOVERED"
    statuses = [a.status for a in c.attempts()]
    assert statuses == ["INTERRUPTED", "SUCCESS"]
    outage = c.outages()[0]
    assert outage.failed_attempts == 1
    assert outage.restored_at_ms is not None
    assert outage.coverage_verified is False
    with pytest.raises(WorkerLeaseConflict):
        c._complete(prev.attempt_id, status="FAILED",
                    failure_code="OSError", lease=lost)


@pytest.mark.asyncio
async def test_fenced_heartbeat_renews_active_capture(tmp_path):
    c, fence, adapter, journal, _, _ = rig(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def capture(sid, seq):
        entered.set()
        await release.wait()
        return await captured(adapter, sid)

    worker = asyncio.create_task(c.run_fenced_batch(
        capture, holder_id="slow_owner", max_attempts=1,
        heartbeat_interval_s=0.01,
    ))
    await asyncio.wait_for(entered.wait(), 1)
    generation = fence.status().generation
    await asyncio.sleep(0.06)
    assert fence.status().active
    assert fence.status().generation == generation
    release.set()
    assert (await worker).status == "RECOVERED"


@pytest.mark.asyncio
async def test_lease_loss_during_capture_cannot_write_success(tmp_path):
    c, fence, adapter, _, clock, _ = rig(tmp_path)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def paused(sid, seq):
        entered.set()
        await release.wait()
        return await captured(adapter, sid)

    job = asyncio.create_task(c.run_fenced_batch(
        paused, holder_id="worker1", max_attempts=1,
        heartbeat_interval_s=0.01,
    ))
    await asyncio.wait_for(entered.wait(), 1)
    clock.jump(1500)
    new = fence.acquire(holder_id="worker2")
    release.set()
    with pytest.raises(WorkerLeaseConflict):
        await job
    assert c.attempts()[0].status == "STARTED"
    assert c.outages() == []
    assert c.reconcile_crashed_attempts(lease=new) == 1


@pytest.mark.asyncio
async def test_durable_fence_rejects_wrong_holder_and_expired_claim(tmp_path):
    c, fence, _, _, clock, _ = rig(tmp_path)
    ticket = fence.acquire(holder_id="owner")
    clock.jump(1000)
    with pytest.raises(WorkerLeaseConflict, match="expired"):
        c.begin_attempt(lease=ticket)
    with pytest.raises(WorkerLeaseConflict):
        fence.renew(ticket)


def test_invalid_fence_config_and_holder_names(tmp_path):
    journal = OfflineSpotCaptureJournal(
        tmp_path / "invalid.db",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    for lease_ms in (0, 999, 300001, True):
        with pytest.raises(ValueError, match="lease_ms"):
            OfflineSpotWorkerFence(
                journal, controller_id="c", lease_ms=lease_ms,
            )
    fence = OfflineSpotWorkerFence(
        journal, controller_id="c", clock_ms=lambda: BASE, lease_ms=1000,
    )
    with pytest.raises(SpotCaptureError, match="holder_id"):
        fence.acquire(holder_id="../unsafe")


@pytest.mark.asyncio
async def test_missing_fence_and_public_permission_fail_closed(tmp_path):
    source = OfflineSpotCaptureJournal(
        tmp_path / "legacy.db",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    c = OfflineSpotReconnectController(
        source, controller_id="legacy", symbol="BTCUSDT",
    )
    async def never(sid, seq):
        raise AssertionError("must not run")
    with pytest.raises(ValueError, match="requires configured"):
        await c.run_fenced_batch(never, holder_id="worker")
    fc, _, adapter, _, _, _ = rig(tmp_path)
    with pytest.raises(PermissionError, match="explicit"):
        await fc.run_fenced_public(adapter, holder_id="worker")
    assert fc.attempts() == []


def test_reality_mode_denies_lease_mutation_and_inspection(tmp_path):
    flag = [QuantMode.SIMULATION]
    _, fence, _, _, _, _ = rig(tmp_path, mode=flag)
    ticket = fence.acquire(holder_id="worker_one")
    flag[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        fence.status()
    with pytest.raises(RuntimeError, match="SIMULATION"):
        fence.renew(ticket)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        fence.release(ticket)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        fence.acquire(holder_id="worker_two")
