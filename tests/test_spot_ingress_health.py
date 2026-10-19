"""Read-only capture/outage and projection-backlog metrics regressions."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_ingress_health import OfflineSpotIngressHealthMonitor
from infrastructure.spot_public_capture_adapter import OptInSpotIngressAdapter
from infrastructure.spot_reconnect_controller import OfflineSpotReconnectController
from infrastructure.spot_worker_fence import OfflineSpotWorkerFence

BASE = 1_780_000_000_000


class Clock:
    def __init__(self):
        self.time = BASE

    def __call__(self):
        self.time += 10
        return self.time

    def jump(self, ms):
        self.time += ms


def rig(tmp_path, *, mode=None):
    state = [QuantMode.SIMULATION] if mode is None else mode
    clock = Clock()
    journal = OfflineSpotCaptureJournal(
        tmp_path / "market.db", mode_provider=lambda: state[0],
    )
    lease = OfflineSpotWorkerFence(
        journal, controller_id="btc_capture",
        clock_ms=clock, lease_ms=1000,
    )
    controller = OfflineSpotReconnectController(
        journal, controller_id="btc_capture",
        symbol="BTCUSDT", clock_ms=clock, worker_fence=lease,
    )
    monitor = OfflineSpotIngressHealthMonitor(
        controller, worker_fence=lease,
    )
    return monitor, controller, lease, journal, clock, state


async def capture(adapter, session_id):
    async def websocket():
        yield json.dumps({
            "stream": "btcusdt@depth@100ms",
            "data": {
                "e": "depthUpdate", "s": "BTCUSDT",
                "E": BASE, "U": 100, "u": 102,
                "b": [["100", "3"]], "a": [],
            },
        }).encode()
        await asyncio.sleep(0.001)
    async def rest(symbol, limit):
        return json.dumps({
            "lastUpdateId": 100,
            "bids": [["100", "2"]], "asks": [["102", "3"]],
        }).encode()
    return await adapter.capture_open_stream(
        websocket(), rest, session_id=session_id,
    )


def test_zero_data_is_explicit_unknown_not_healthy(tmp_path):
    m, c, fence, j, now, _ = rig(tmp_path)
    result = m.snapshot(now_ms=BASE+100)
    assert result.capture_sessions == 0
    assert result.source_frames == 0
    assert result.captured_bytes == 0
    assert result.attempts_total == 0
    assert result.open_outages == 0
    assert result.worker_lease_active is False
    assert result.unacknowledged_projections is None
    assert "NO_VERIFIED_CAPTURE" in result.findings
    assert "PROJECTION_BACKLOG_UNKNOWN" in result.findings


def test_open_outage_age_and_abandoned_worker_are_reported(tmp_path):
    m, c, fence, _, now, _ = rig(tmp_path)
    claim = fence.acquire(holder_id="worker_one")
    abandoned = c.begin_attempt(lease=claim)
    fence.release(claim)
    h = m.snapshot(now_ms=now.time + 60)
    assert h.unfinished_attempts == 1
    assert h.worker_lease_active is False
    assert "UNFINISHED_CAPTURE_ATTEMPT" in h.findings
    assert "NO_ACTIVE_FENCED_WORKER" in h.findings

    # Reacquire a new generation, explicitly reconcile the interrupted attempt.
    replacement = fence.acquire(holder_id="worker_two")
    assert c.reconcile_crashed_attempts(lease=replacement) == 1
    fence.release(replacement)
    now.jump(40000)
    h = m.snapshot(max_outage_age_ms=1000)
    assert h.attempts_interrupted == 1
    assert h.open_outages == 1
    assert h.unresolved_outage_age_ms >= 40000
    assert "OUTAGE_AGE_THRESHOLD" in h.findings
    assert h.latest_success_at_ms is None


@pytest.mark.asyncio
async def test_health_counts_successful_source_capture_and_staleness(tmp_path):
    m, c, fence, j, now, _ = rig(tmp_path)
    adapter = OptInSpotIngressAdapter(
        j, symbol="BTCUSDT", clock_ms=now,
    )
    result = await c.run_fenced_batch(
        lambda sid, seq: capture(adapter, sid),
        holder_id="worker1", max_attempts=1,
        heartbeat_interval_s=0.02,
    )
    assert result.status == "RECOVERED"
    snap = m.snapshot(now_ms=now.time + 50)
    assert snap.capture_sessions == 1
    assert snap.source_frames == 2
    assert snap.captured_bytes > 0
    assert snap.attempts_total == 1
    assert snap.attempts_success == 1
    assert snap.open_outages == 0
    assert snap.latest_success_at_ms is not None
    assert "NO_VERIFIED_CAPTURE" not in snap.findings
    assert "LAST_VERIFIED_CAPTURE_STALE" not in snap.findings
    later = m.snapshot(now_ms=now.time + 60000,
                       max_recent_capture_age_ms=10)
    assert "LAST_VERIFIED_CAPTURE_STALE" in later.findings


def test_explicit_projection_receiver_backlog_only_when_tables_exist(tmp_path):
    m, _, _, journal, clock, _ = rig(tmp_path)
    assert m.snapshot(
        source_consumer="producer", receiver="risk",
        now_ms=BASE + 500,
    ).unacknowledged_projections is None
    with sqlite3.connect(journal.path) as conn:
        conn.execute("""
            CREATE TABLE offline_market_projections (
                consumer TEXT NOT NULL, ingest_id INTEGER NOT NULL,
                available_at_ms INTEGER NOT NULL,
                PRIMARY KEY (consumer, ingest_id)
            )
        """)
        conn.execute("""
            CREATE TABLE offline_market_projection_inbox_receipts (
                source_consumer TEXT NOT NULL,
                receiver TEXT NOT NULL, ingest_id INTEGER NOT NULL,
                PRIMARY KEY (source_consumer,receiver,ingest_id)
            )
        """)
        conn.executemany("""
            INSERT INTO offline_market_projections VALUES (?,?,?)
        """, [("producer",1,BASE+100),("producer",2,BASE+200),
              ("producer",3,BASE+900),("other",4,BASE+100)])
        conn.execute("""
            INSERT INTO offline_market_projection_inbox_receipts
            VALUES ('producer','risk',1)
        """)
    h = m.snapshot(
        now_ms=BASE+300,
        source_consumer="producer", receiver="risk",
        max_pending_projections=1,
    )
    assert h.unacknowledged_projections == 1
    assert "PROJECTION_BACKLOG_THRESHOLD" in h.findings
    different = m.snapshot(
        now_ms=BASE+300,
        source_consumer="producer", receiver="signals",
        max_pending_projections=3,
    )
    assert different.unacknowledged_projections == 2
    assert "PROJECTION_BACKLOG_THRESHOLD" not in different.findings
    later = m.snapshot(
        now_ms=BASE+1000,
        source_consumer="producer", receiver="risk",
    )
    assert later.unacknowledged_projections == 2


@pytest.mark.parametrize("bad", [
    {"max_recent_capture_age_ms": -1},
    {"max_outage_age_ms": -1},
    {"max_pending_projections": 0},
    {"max_pending_projections": True},
    {"source_consumer": "producer"},
    {"receiver": "risk"},
    {"source_consumer": "../escape", "receiver": "risk"},
])
def test_invalid_metric_queries_fail_closed(tmp_path, bad):
    m, _, _, _, _, _ = rig(tmp_path)
    with pytest.raises((ValueError, Exception)):
        m.snapshot(now_ms=BASE + 100, **bad)


def test_read_only_mode_denied_in_reality(tmp_path):
    mode = [QuantMode.SIMULATION]
    m, _, _, _, _, _ = rig(tmp_path, mode=mode)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        m.snapshot(now_ms=BASE + 100)


def test_health_monitor_rejects_mismatched_fence(tmp_path):
    m, c, fence, journal, now, _ = rig(tmp_path)
    other = OfflineSpotWorkerFence(
        journal, controller_id="different",
        clock_ms=now, lease_ms=1000,
    )
    with pytest.raises(ValueError, match="same local controller"):
        OfflineSpotIngressHealthMonitor(c, worker_fence=other)
