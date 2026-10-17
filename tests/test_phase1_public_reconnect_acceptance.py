"""Controlled real-transport reconnect probe: no network during CI."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.mode import QuantMode
from ci.phase1_public_spot_acceptance import ObservedClock
from ci.phase1_public_reconnect_acceptance import (
    main, run, parser, validate_args,
)
from infrastructure.spot_public_capture_adapter import (
    OptInSpotIngressAdapter, SpotTransportError,
)

BASE = int(time.time_ns() // 1_000_000)


def opts(tmp_path, *extra):
    return parser().parse_args([
        "--output-dir", str(tmp_path / "evidence"),
        *extra,
    ])


def test_requires_explicit_public_network_permission_before_any_side_effect(tmp_path, capsys):
    assert main(["--output-dir", str(tmp_path / "closed")]) == 2
    assert not (tmp_path / "closed").exists()
    assert "PermissionError" in capsys.readouterr().err


@pytest.mark.parametrize("extra", [
    ("--interrupt-after-frames","0"),
    ("--interrupt-after-frames","500"),
    ("--max-frames","5000"),
    ("--max-frames","10","--interrupt-after-frames","10"),
    ("--timeout-s","1"),
    ("--max-clock-rtt-ms","0"),
    ("--max-clock-offset-ms","0"),
])
def test_invalid_bounds_rejected_before_network(tmp_path, extra):
    with pytest.raises(ValueError):
        validate_args(opts(tmp_path, "--allow-public-market-data", *extra))


def _frame_trade(agg):
    return json.dumps({
        "stream":"btcusdt@aggTrade",
        "data":{
            "e":"aggTrade", "s":"BTCUSDT",
            "a":agg, "E":BASE, "T":BASE-10,
            "f":agg,"l":agg, "p":"101", "q":"0.02", "m":False,
        },
    }).encode()


def _frame_depth():
    return json.dumps({
        "stream":"btcusdt@depth@100ms",
        "data":{
            "e":"depthUpdate", "s":"BTCUSDT",
            "E":BASE, "U":100, "u":102,
            "b":[["100","4"]], "a":[],
        },
    }).encode()


@pytest.mark.asyncio
async def test_forced_public_socket_disconnect_is_terminal_and_closes_original(tmp_path, monkeypatch):
    from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
    class FakeWS:
        async def __aenter__(self):
            async def stream():
                yield _frame_depth()
                for idx in range(20):
                    await asyncio.sleep(0.001)
                    yield _frame_trade(500 + idx)
            return stream()
        async def __aexit__(self, *args):
            return False
    class FakeHTTP:
        def __init__(self, **kwargs):
            assert kwargs["verify"] is True
            assert kwargs["trust_env"] is False
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                content=json.dumps({
                    "lastUpdateId":100,
                    "bids":[["100","3"]], "asks":[["102","4"]],
                }).encode(),
            )
    import websockets
    import httpx
    monkeypatch.setattr(websockets, "connect", lambda *a, **kw: FakeWS())
    monkeypatch.setattr(httpx, "AsyncClient", FakeHTTP)
    journal = OfflineSpotCaptureJournal(
        tmp_path / "first_failed.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )
    adapter = OptInSpotIngressAdapter(journal, symbol="BTCUSDT")
    with pytest.raises(SpotTransportError) as err:
        await adapter.capture_public_once(
            allow_network=True, session_id="forced_01",
            inject_disconnect_after_frames=10, max_frames=20,
        )
    assert err.value.reason == "INJECTED_WS_DISCONNECT"
    with journal._connect() as conn:
        session = conn.execute("""
            SELECT closed_at_ms FROM offline_spot_capture_sessions
            WHERE session_id='forced_01'
        """).fetchone()
    assert session["closed_at_ms"] is not None
    assert len(journal.replay_as_of("forced_01", BASE + 60000)) >= 10


@pytest.mark.asyncio
async def test_controlled_reconnect_fails_then_fresh_real_protocol_bridge_succeeds(
    tmp_path, monkeypatch,
):
    import ci.phase1_public_reconnect_acceptance as cli
    import websockets
    import httpx

    async def fake_clock(**kwargs):
        return ObservedClock(BASE, 0, 80, 40)

    class FakeWS:
        async def __aenter__(self):
            async def stream():
                yield _frame_depth()
                for idx in range(25):
                    await asyncio.sleep(0.001)
                    yield _frame_trade(500 + idx)
            return stream()
        async def __aexit__(self, *args):
            return False

    class FakeHTTP:
        def __init__(self, **kwargs):
            assert kwargs["verify"] is True
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, *args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                content=json.dumps({
                    "lastUpdateId":100,
                    "bids":[["100","3"]], "asks":[["102","4"]],
                }).encode(),
            )
    monkeypatch.setattr(cli, "measure_public_clock", fake_clock)
    monkeypatch.setattr(websockets, "connect", lambda *a, **kw: FakeWS())
    monkeypatch.setattr(httpx, "AsyncClient", FakeHTTP)
    params = opts(
        tmp_path, "--allow-public-market-data",
        "--interrupt-after-frames","10",
        "--max-frames","20",
    )
    report = await run(params)
    assert report["acceptance"] == "PASSED_CONTROLLED_RECONNECT_WITH_UNRECOVERED_HISTORY_GAP"
    assert report["result"] == "RECOVERED"
    assert report["source_sessions_distinct"] is True
    assert report["attempts"][0]["failure_code"] == "INJECTED_WS_DISCONNECT"
    assert report["attempts"][1]["status"] == "SUCCESS"
    assert report["first_session_original_count"] >= 10
    assert report["second_session_verified_depth"] == "SYNCED"
    assert report["history_continuity_verified"] is False
    assert report["gap_imputed_or_backfilled"] is False
    assert report["orders_submitted"] == 0
    assert report["outages"][0]["restored_at_ms"] is not None
    assert report["outages"][0]["coverage_verified"] is False
    assert (params.output_dir / "phase1_reconnect_acceptance.json").exists()


@pytest.mark.asyncio
async def test_invalid_explicit_disconnect_refuses_without_socket(tmp_path):
    from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
    journal = OfflineSpotCaptureJournal(
        tmp_path / "bad.sqlite",mode_provider=lambda:QuantMode.SIMULATION,
    )
    adapter = OptInSpotIngressAdapter(journal, symbol="BTCUSDT")
    for amount in (True,0,-1,20,100,1.0,"10"):
        with pytest.raises(ValueError, match="injected disconnect"):
            await adapter.capture_public_once(
                allow_network=True, max_frames=20,
                inject_disconnect_after_frames=amount,
            )
