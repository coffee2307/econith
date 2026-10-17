"""Verified-only historical runtime: real app bypass controls and durable ACK."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import httpx
import pytest

from core.event_bus import RawMarketAdmissionDenied
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_verified_cutover_gate import (
    VerifiedIngressNotReady, enforce_verified_only_spot_ingress,
)
from infrastructure.spot_verified_research_runtime import (
    start_verified_research_runtime,
)

BASE = 1_780_000_000_000
WS = "wss://data-stream.binance.vision/stream"
REST = "https://data-api.binance.vision/api/v3/depth"


def _frame(frame):
    stream = ("btcusdt@aggTrade" if frame["e"] == "aggTrade"
              else "btcusdt@depth@100ms")
    return json.dumps({"stream":stream,"data":frame}).encode()


def _original(tmp_path, *, has_snapshot=True, closed=True):
    src = tmp_path / "source.sqlite"
    journal = OfflineSpotCaptureJournal(
        src, mode_provider=lambda: QuantMode.SIMULATION,
    )
    sid = "rec_verified_replay"
    journal.start_session(
        sid, symbol="BTCUSDT",
        source="binance_public_simulation",
        ws_endpoint=WS,opened_at_ms=BASE+50,
    )
    journal.record_ws(
        sid, _frame({
            "e":"aggTrade","E":BASE,"T":BASE-10,"s":"BTCUSDT",
            "a":500,"f":500,"l":500,"p":"101","q":"0.5","m":False,
        }), stream_kind="aggTrade",received_at_ms=BASE+100,
    )
    if has_snapshot:
        journal.record_ws(
            sid, _frame({
                "e":"depthUpdate","E":BASE,"s":"BTCUSDT",
                "U":100,"u":102,"b":[["100","3"]],"a":[],
            }),stream_kind="depthUpdate",received_at_ms=BASE+110,
        )
        journal.record_snapshot(
            sid, json.dumps({
                "lastUpdateId":100,
                "bids":[["100","2"]],"asks":[["102","4"]],
            }).encode(),rest_endpoint=REST,request_symbol="BTCUSDT",
            request_started_at_ms=BASE+120,received_at_ms=BASE+130,
        )
    journal.record_ws(
        sid, _frame({
            "e":"aggTrade","E":BASE,"T":BASE-10,"s":"BTCUSDT",
            "a":501,"f":501,"l":501,"p":"102","q":"0.7","m":True,
        }),stream_kind="aggTrade",received_at_ms=BASE+150,
    )
    if closed:
        journal.close_session(sid,closed_at_ms=BASE+200)
    return src,sid


@pytest.mark.asyncio
async def test_verified_research_bus_only_sees_receipt_provenance(tmp_path):
    original, sid = _original(tmp_path)
    runtime = await start_verified_research_runtime(
        original_db=original,session_id=sid,output_dir=tmp_path/"generated",
    )
    try:
        status = runtime.status
        assert status.market_data_policy == "verified_only"
        assert status.mode == "SIMULATION"
        assert status.raw_count == 4
        assert status.agg_trade_count == 2
        assert status.emitted_raw_trades == 2
        assert status.emitted_depth_books == 1
        assert status.observed_tickers == 2
        assert status.observed_volume_deltas == 2
        assert status.observed_orderbook_imbalance == 1
        assert status.producer_acks == status.receiver_receipts == 2
        assert status.local_receiver_authenticated
        assert status.original_bytes_read_only
        assert status.historical_replay_only
        assert not status.continuous_live_market
        assert not status.execution_started
        assert not status.legacy_streamer_started
        assert status.orders_submitted == 0
        assert not status.history_continuity_verified
        with pytest.raises(RawMarketAdmissionDenied):
            await runtime.bus.publish(
                "md.aggTrade",frame={"a":502},source="verified_closed_session_replay",
            )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_unsynced_or_open_source_cannot_start_research_subscribers(tmp_path):
    for k,kwargs in enumerate(({"has_snapshot":False},{"closed":False})):
        source,sid = _original(tmp_path/f"fixture{k}",**kwargs)
        with pytest.raises((ValueError,RuntimeError)):
            await start_verified_research_runtime(
                original_db=source,session_id=sid,
                output_dir=tmp_path/f"out{k}",
            )


def test_verified_only_policy_must_include_full_explicit_source(monkeypatch,tmp_path):
    for name in (
        "ECONITH_SPOT_VERIFIED_REPLAY_DB",
        "ECONITH_SPOT_VERIFIED_REPLAY_SESSION",
        "ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR",
    ):
        monkeypatch.delenv(name,raising=False)
    with pytest.raises(VerifiedIngressNotReady,match="startup stopped"):
        enforce_verified_only_spot_ingress("verified_only")
    original,sid = _original(tmp_path)
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_DB",str(original))
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_SESSION",sid)
    monkeypatch.setenv(
        "ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR",str(tmp_path/"new_out"),
    )
    assert enforce_verified_only_spot_ingress("verified_only") == "verified_only"
    (tmp_path/"new_out").mkdir()
    (tmp_path/"new_out"/"occupied.txt").write_text("reserved")
    with pytest.raises(VerifiedIngressNotReady,match="startup stopped"):
        enforce_verified_only_spot_ingress("verified_only")


@pytest.mark.asyncio
async def test_main_verified_only_lifespan_protects_all_legacy_http(monkeypatch,tmp_path):
    source,sid = _original(tmp_path)
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY","verified_only")
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_DB",str(source))
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_SESSION",sid)
    monkeypatch.setenv(
        "ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR",str(tmp_path/"app_out"),
    )
    from main import app,components
    async with app.router.lifespan_context(app):
        assert "verified_research_runtime" in components
        assert "streamer" not in components
        assert "ccxt_bridge" not in components
        assert "engine" not in components
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://research.local",
        ) as http:
            ok = await http.get("/api/v1/market/verified/status")
            assert ok.status_code == 200
            body = ok.json()
            assert body["producer_acks"] == body["receiver_receipts"] == 2
            assert body["execution_started"] is False
            assert body["legacy_streamer_started"] is False
            assert body["history_continuity_verified"] is False
            assert body["market_data_policy"] == "verified_only"
            for url,method in (
                ("/api/v1/health","GET"),
                ("/api/v1/metrics","GET"),
                ("/api/v1/mode","POST"),
                ("/api/v1/quant/routing/profile","POST"),
                ("/openapi.json","GET"),
            ):
                response = await http.request(method,url)
                assert response.status_code == 404,(url,response.status_code)
    assert not components
    assert app.state.verified_only_research is False


@pytest.mark.asyncio
async def test_main_does_not_boot_on_bad_snapshot_or_create_execution(monkeypatch,tmp_path):
    source,sid=_original(tmp_path,has_snapshot=False)
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY","verified_only")
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_DB",str(source))
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_SESSION",sid)
    monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR",str(tmp_path/"out"))
    from main import app,components
    with pytest.raises((ValueError,RuntimeError)):
        async with app.router.lifespan_context(app):
            pytest.fail("unsynced source reached startup")
    assert "streamer" not in components
    assert "ccxt_bridge" not in components
    assert "engine" not in components


@pytest.mark.asyncio
async def test_verified_only_denies_legacy_metrics_websocket_without_accept():
    from types import SimpleNamespace
    from main import app, stream_metrics

    observed = []
    class FakeSocket:
        def __init__(self):
            self.app = app
        async def close(self, *, code):
            observed.append(("close",code))
        async def accept(self):
            observed.append(("accept",None))
            raise AssertionError("legacy WebSocket must not be accepted")

    previous = getattr(app.state,"verified_only_research",False)
    app.state.verified_only_research = True
    try:
        await stream_metrics(FakeSocket())
        assert observed == [("close",1008)]
    finally:
        app.state.verified_only_research = previous
