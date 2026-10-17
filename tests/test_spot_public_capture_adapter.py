"""Fault-injected opt-in Spot transport; never require live network for CI."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from core.mode import QuantMode
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportError,
)

BASE = 1_780_000_000_000


def clock():
    step = {"value": BASE}
    def now():
        step["value"] += 10
        return step["value"]
    return now


def depth(first=100, final=102, symbol="BTCUSDT"):
    return {
        "e": "depthUpdate", "E": BASE, "s": symbol,
        "U": first, "u": final,
        "b": [["100", "3"]], "a": [],
    }


def trade(i=1, symbol="BTCUSDT"):
    return {
        "e": "aggTrade", "E": BASE, "T": BASE,
        "s": symbol, "a": i, "f": i, "l": i,
        "p": "100", "q": "0.02", "m": False,
    }


def raw(data, *, stream=None):
    if stream is None:
        stream = "btcusdt@depth@100ms" if data["e"] == "depthUpdate" else "btcusdt@aggTrade"
    return json.dumps({"stream": stream, "data": data}, separators=(",", ":")).encode()


def snapshot(last=100):
    return json.dumps({
        "lastUpdateId": last,
        "bids": [["100", "2"], ["99", "3"]],
        "asks": [["102", "2"], ["103", "1"]],
    }).encode()


def make(tmp_path, *, queue_capacity=16, mode=None, max_frames_per_session=5000):
    state = [QuantMode.SIMULATION] if mode is None else mode
    journal = OfflineSpotCaptureJournal(
        tmp_path / "events.sqlite",
        mode_provider=lambda: state[0],
        max_frames_per_session=max_frames_per_session,
    )
    return OptInSpotIngressAdapter(
        journal, symbol="BTCUSDT", clock_ms=clock(),
        queue_capacity=queue_capacity,
    ), journal, state


def feed(*payloads, gap=0):
    async def stream():
        for payload in payloads:
            if gap:
                await asyncio.sleep(gap)
            yield payload
    return stream()


async def rest(symbol, limit):
    assert (symbol, limit) == ("BTCUSDT", 1000)
    await asyncio.sleep(0)
    return snapshot()


@pytest.mark.asyncio
async def test_opt_in_transport_preserves_original_ws_and_rest_and_syncs_book(tmp_path):
    adapter, journal, _ = make(tmp_path)
    d = raw(depth())
    t = raw(trade(10))
    received = await adapter.capture_open_stream(
        feed(d, t, raw(depth(103, 104)), gap=0.001),
        rest, session_id="session_01",
        max_frames=10,
    )
    assert received.websocket_frames == 3
    assert received.snapshot_captures == 1
    assert received.depth_status == "SYNCED"
    assert received.last_update_id == 104
    assert received.queue_high_watermark >= 1
    evidence = journal.replay_as_of("session_01", received.last_received_at_ms)
    assert [e.kind for e in evidence].count("snapshot") == 1
    assert [e.raw for e in evidence if e.kind == "depthUpdate"] == [
        d, raw(depth(103, 104)),
    ]
    replay = OfflineSpotCaptureReplayer(journal).replay(
        "session_01", as_of_ms=received.last_received_at_ms,
    )
    assert replay.book is not None and replay.book.last_update_id == 104
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute(
            "SELECT closed_at_ms FROM offline_spot_capture_sessions"
        ).fetchone()[0] >= received.last_received_at_ms


@pytest.mark.asyncio
async def test_unexpected_partial_depth20_fails_closed_and_session_is_closed(tmp_path):
    adapter, journal, _ = make(tmp_path)
    with pytest.raises(SpotTransportError, match="unexpected combined"):
        await adapter.capture_open_stream(
            feed(raw(depth(), stream="btcusdt@depth20@100ms")),
            rest, session_id="bad_stream",
        )
    assert journal.replay_as_of("bad_stream", BASE + 3000) == []
    with pytest.raises(Exception, match="closed"):
        journal.record_ws(
            "bad_stream", raw(depth()), stream_kind="depthUpdate",
            received_at_ms=BASE + 5000,
        )


@pytest.mark.asyncio
async def test_queue_backpressure_never_discards_and_marks_session_invalid(tmp_path):
    adapter, journal, _ = make(tmp_path, queue_capacity=2)
    with pytest.raises(SpotTransportError, match="backpressure"):
        await adapter.capture_open_stream(
            feed(*(raw(trade(i)) for i in range(100, 140))),
            rest, session_id="overflow",
        )
    rows = journal.replay_as_of("overflow", BASE + 5000)
    assert len(rows) <= 2
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute(
            "SELECT closed_at_ms FROM offline_spot_capture_sessions WHERE session_id=?",
            ("overflow",),
        ).fetchone()[0] is not None


@pytest.mark.asyncio
async def test_snapshot_network_failure_cannot_become_synced_book(tmp_path):
    adapter, journal, _ = make(tmp_path)
    async def broken(symbol, limit):
        raise OSError("connection dropped")
    with pytest.raises(SpotTransportError, match="OSError"):
        await adapter.capture_open_stream(
            feed(raw(depth()), gap=0.001),
            broken, session_id="rest_dropped",
        )
    records = journal.replay_as_of("rest_dropped", BASE + 5000)
    assert len(records) == 1 and records[0].kind == "depthUpdate"


@pytest.mark.asyncio
async def test_snapshot_too_old_refuses_to_claim_synced(tmp_path):
    adapter, journal, _ = make(tmp_path)
    async def old(symbol, limit):
        return snapshot(50)
    with pytest.raises(SpotTransportError, match="usable synchronized"):
        await adapter.capture_open_stream(
            feed(raw(depth(100, 102)), gap=0.001),
            old, session_id="old_snapshot",
        )
    assert len(journal.replay_as_of("old_snapshot", BASE + 5000)) == 2


@pytest.mark.asyncio
async def test_trade_only_feed_refuses_incomplete_depth(tmp_path):
    adapter, journal, _ = make(tmp_path)
    with pytest.raises(SpotTransportError, match="no captured REST"):
        await adapter.capture_open_stream(
            feed(raw(trade(20))), rest, session_id="no_depth",
        )
    assert len(journal.replay_as_of("no_depth", BASE + 5000)) == 1


@pytest.mark.asyncio
async def test_rest_response_failure_is_never_silently_ignored(tmp_path):
    adapter, _, _ = make(tmp_path)
    async def wrong(symbol, limit):
        return b'{"unexpected":"error"}'
    with pytest.raises(Exception, match="snapshot"):
        await adapter.capture_open_stream(
            feed(raw(depth()), gap=0.001),
            wrong, session_id="rest_invalid",
        )


@pytest.mark.asyncio
async def test_injected_db_insert_failure_aborts_and_preserves_evidence(tmp_path):
    adapter, journal, _ = make(tmp_path)
    with sqlite3.connect(journal.path) as conn:
        conn.execute("""
            CREATE TRIGGER reject_ingress_capture BEFORE INSERT
            ON offline_spot_capture_records
            BEGIN SELECT RAISE(ABORT, 'injected full disk'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="injected full disk"):
        await adapter.capture_open_stream(
            feed(raw(depth()), gap=0.001),
            rest, session_id="disk_full",
        )
    assert journal.replay_as_of("disk_full", BASE + 5000) == []
    with sqlite3.connect(journal.path) as conn:
        conn.execute("DROP TRIGGER reject_ingress_capture")
    # A NEW session is necessary, the old one must not be continued.
    result = await adapter.capture_open_stream(
        feed(raw(depth()), gap=0.001), rest,
        session_id="after_disk_error",
    )
    assert result.depth_status == "SYNCED"


@pytest.mark.asyncio
async def test_new_session_not_reused_and_can_recover_after_failed_session(tmp_path):
    adapter, journal, _ = make(tmp_path)
    with pytest.raises(SpotTransportError):
        await adapter.capture_open_stream(
            feed(raw(depth(), stream="btcusdt@depth20@100ms")),
            rest, session_id="session_01",
        )
    result = await adapter.capture_open_stream(
        feed(raw(depth()), gap=0.001), rest,
        session_id="session_02",
    )
    assert result.depth_status == "SYNCED"
    assert len(journal.replay_as_of("session_02", BASE + 5000)) == 2
    with pytest.raises(sqlite3.IntegrityError):
        await adapter.capture_open_stream(
            feed(raw(depth())), rest, session_id="session_01",
        )


@pytest.mark.asyncio
async def test_mode_change_during_capture_hard_stops(tmp_path):
    mode = [QuantMode.SIMULATION]
    adapter, journal, _ = make(tmp_path, mode=mode)
    async def turning():
        yield raw(depth())
        mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        await adapter.capture_open_stream(
            turning(), rest, session_id="mode_change",
        )
    mode[0] = QuantMode.SIMULATION
    assert journal.replay_as_of("mode_change", BASE + 5000) == []


@pytest.mark.asyncio
async def test_network_permission_fail_closed_before_external_import(tmp_path):
    adapter, journal, mode = make(tmp_path)
    with pytest.raises(PermissionError, match="explicit opt-in"):
        await adapter.capture_public_once(allow_network=False)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        await adapter.capture_public_once(allow_network=True)
    assert not journal.path.stat().st_size == 0


@pytest.mark.asyncio
async def test_public_transport_pins_tls_hosts_and_get_only_via_fake_clients(
    tmp_path, monkeypatch,
):
    adapter, journal, _ = make(tmp_path)
    calls = {"ws": [], "http": []}

    class FakeWS:
        async def __aenter__(self):
            return feed(raw(depth()), gap=0.001)
        async def __aexit__(self, *args):
            return False

    def ws_connect(url, **kwargs):
        calls["ws"].append((url, kwargs))
        return FakeWS()

    class FakeHTTP:
        def __init__(self, **kwargs):
            calls["http"].append(("open", kwargs))
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, endpoint, *, params):
            calls["http"].append(("get", endpoint, params))
            return SimpleNamespace(status_code=200, content=snapshot())

    # Explicit network mode, but transports are mocked; no internet request.
    import websockets
    import httpx
    monkeypatch.setattr(websockets, "connect", ws_connect)
    monkeypatch.setattr(httpx, "AsyncClient", FakeHTTP)
    result = await adapter.capture_public_once(
        allow_network=True, session_id="tls_fake",
    )
    assert result.depth_status == "SYNCED"
    assert calls["ws"][0][0] == (
        "wss://data-stream.binance.vision/stream"
        "?streams=btcusdt@aggTrade/btcusdt@depth@100ms"
    )
    assert calls["ws"][0][1]["max_size"] == journal.max_body_bytes
    assert calls["http"][0][1]["verify"] is True
    assert calls["http"][0][1]["trust_env"] is False
    assert calls["http"][0][1]["follow_redirects"] is False
    assert calls["http"][1] == (
        "get", "https://data-api.binance.vision/api/v3/depth",
        {"symbol": "BTCUSDT", "limit": 1000},
    )


@pytest.mark.asyncio
async def test_bounded_lifetime_and_frame_count_do_not_return_false_success(tmp_path):
    adapter, _, _ = make(tmp_path)
    with pytest.raises(SpotTransportError, match="frame bound"):
        await adapter.capture_open_stream(
            feed(raw(trade(1)), raw(trade(2)), raw(trade(3))),
            rest, session_id="too_many", max_frames=2,
        )

    async def stalled():
        yield raw(depth())
        await asyncio.sleep(10)

    async def hanging_snapshot(symbol, limit):
        await asyncio.sleep(10)
        return snapshot()

    with pytest.raises(SpotTransportError, match="timed out"):
        await adapter.capture_open_stream(
            stalled(), hanging_snapshot,
            session_id="timeout", timeout_s=0.05,
        )


@pytest.mark.asyncio
async def test_finite_frame_bound_success_only_after_synced_snapshot(tmp_path):
    adapter, journal, _ = make(tmp_path)
    first = raw(depth())
    second = raw(trade(10))
    third = raw(trade(11))
    result = await adapter.capture_open_stream(
        feed(first, second, third, gap=0.001), rest,
        session_id="bounded_good", max_frames=2,
    )
    assert result.websocket_frames == 2
    assert result.snapshot_captures == 1
    assert result.depth_status == "SYNCED"
    assert result.last_update_id == 102
    originals = journal.replay_as_of("bounded_good", result.last_received_at_ms)
    assert [r.raw for r in originals if r.kind == "aggTrade"] == [second]
    assert len(originals) == 3


@pytest.mark.asyncio
async def test_unsynced_frame_limit_exposes_static_diagnostic_reason(tmp_path):
    adapter, journal, _ = make(tmp_path)
    with pytest.raises(SpotTransportError, match="frame bound") as err:
        await adapter.capture_open_stream(
            feed(raw(trade(1)), raw(trade(2)), raw(trade(3))),
            rest, session_id="bounded_unhealthy", max_frames=2,
        )
    assert err.value.reason == "FRAME_LIMIT_UNSYNCED"
    assert len(journal.replay_as_of("bounded_unhealthy", BASE + 5000)) == 2


@pytest.mark.asyncio
async def test_snapshot_failure_exposes_static_diagnostic_reason(tmp_path):
    adapter, _, _ = make(tmp_path)
    async def failure(*args):
        raise OSError("private_url_or_user_data")
    with pytest.raises(SpotTransportError) as err:
        await adapter.capture_open_stream(
            feed(raw(depth()), gap=0.001),
            failure, session_id="rest_error_reason",
        )
    assert err.value.reason == "REST_SNAPSHOT_FAILED"
