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
        assert status.durable_derived_research_receipts == 5
        from infrastructure.market_derived_research_receipts import (
            OfflineDerivedResearchReceiptLedger,
        )
        ledger=OfflineDerivedResearchReceiptLedger(
            tmp_path/"generated"/"derived_consumer_receipts.sqlite",
            mode_provider=lambda:QuantMode.SIMULATION,
        )
        assert ledger.verified_count(sid,"md.ticker")==2
        assert ledger.verified_count(sid,"indicator.volume_delta")==2
        assert ledger.verified_count(sid,"indicator.obi")==1
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
            assert body["durable_derived_research_receipts"] == 5
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


@pytest.mark.asyncio
async def test_closed_source_restart_reuses_durable_derived_receipts(tmp_path):
    """A new research graph verifies the source again and idempotently replays ACKs."""
    from infrastructure.market_derived_research_receipts import (
        OfflineDerivedResearchReceiptLedger,
    )
    original,sid=_original(tmp_path/"source")
    ledger_path=(tmp_path/"shared-verified-receipts.sqlite").resolve()
    OfflineDerivedResearchReceiptLedger(
        ledger_path, mode_provider=lambda:QuantMode.SIMULATION,
    )
    first=await start_verified_research_runtime(
        original_db=original,session_id=sid,output_dir=tmp_path/"proof_first",
        derived_receipt_db=ledger_path,
    )
    try:
        assert first.status.durable_derived_research_receipts==5
        assert first.status.replayed_derived_research_receipts==0
    finally:
        await first.close()
    # Restart with a fresh independent raw->market->authenticated-receiver
    # proof. Only the consumer-owned durable observer ledger is reused.
    second=await start_verified_research_runtime(
        original_db=original,session_id=sid,output_dir=tmp_path/"proof_restart",
        derived_receipt_db=ledger_path,
    )
    try:
        assert second.status.durable_derived_research_receipts==5
        assert second.status.replayed_derived_research_receipts==5
        assert second.status.orders_submitted==0
        assert second.status.execution_started is False
        assert second.status.continuous_live_market is False
    finally:
        await second.close()
    ledger=OfflineDerivedResearchReceiptLedger(
        ledger_path,mode_provider=lambda:QuantMode.SIMULATION,
    )
    assert ledger.verified_count(sid,"md.ticker")==2
    assert ledger.verified_count(sid,"indicator.volume_delta")==2
    assert ledger.verified_count(sid,"indicator.obi")==1


@pytest.mark.asyncio
async def test_hard_process_exit_mid_derived_observation_is_recovered(tmp_path):
    """Actually kill the research process after two committed derived ACKs.

    The child exits without clean shutdown, WAL and consumer cursor survive.
    A freshly authenticated source verification must precede re-admission.
    """
    import subprocess
    import sys
    import textwrap
    from infrastructure.market_derived_research_receipts import (
        OfflineDerivedResearchReceiptLedger,
    )
    original,sid=_original(tmp_path/"source")
    ledger_path=(tmp_path/"shared-killed-consumer.sqlite").resolve()
    OfflineDerivedResearchReceiptLedger(
        ledger_path,mode_provider=lambda:QuantMode.SIMULATION,
    )
    child_script=textwrap.dedent("""
        import asyncio
        import os
        import sys
        from pathlib import Path
        from infrastructure.market_derived_research_receipts import OfflineDerivedResearchReceiptLedger
        from infrastructure.spot_verified_research_runtime import start_verified_research_runtime

        source,sid,proof,receipts=sys.argv[1:]
        original_accept=OfflineDerivedResearchReceiptLedger.accept
        observed={"count":0}
        def terminate_after_durable_commit(self,**kwargs):
            reply=original_accept(self,**kwargs)
            observed["count"]+=1
            if observed["count"] == 2:
                os._exit(37)
            return reply
        OfflineDerivedResearchReceiptLedger.accept=terminate_after_durable_commit
        asyncio.run(start_verified_research_runtime(
            original_db=Path(source),session_id=sid,
            output_dir=Path(proof),derived_receipt_db=Path(receipts),
        ))
    """)
    child=subprocess.run(
        [sys.executable,"-c",child_script,str(original),sid,
         str(tmp_path/"proof_crashed"),str(ledger_path)],
        capture_output=True,text=True,timeout=40,check=False,
    )
    assert child.returncode==37,(child.returncode,child.stderr[-1000:])
    ledger=OfflineDerivedResearchReceiptLedger(
        ledger_path,mode_provider=lambda:QuantMode.SIMULATION,
    )
    # For the first raw trade, both derived topic handlers committed before
    # process termination. No final OBI nor second trade was acknowledged.
    assert ledger.verified_count(sid,"md.ticker")==1
    assert ledger.verified_count(sid,"indicator.volume_delta")==1
    assert ledger.verified_count(sid,"indicator.obi")==0
    recovered=await start_verified_research_runtime(
        original_db=original,session_id=sid,
        output_dir=tmp_path/"proof_new_process_recovery",
        derived_receipt_db=ledger_path,
    )
    try:
        assert recovered.status.durable_derived_research_receipts==5
        assert recovered.status.replayed_derived_research_receipts==2
        assert recovered.status.orders_submitted==0
    finally:
        await recovered.close()
    assert ledger.verified_count(sid,"md.ticker")==2
    assert ledger.verified_count(sid,"indicator.volume_delta")==2
    assert ledger.verified_count(sid,"indicator.obi")==1


def test_verified_recovery_refuses_original_db_as_receipt_ledger(tmp_path):
    original,sid=_original(tmp_path)
    # Recovery never opens original capture SQLite as the writable
    # durable derived-observer database, even in pure SIMULATION mode.
    async def attempt():
        await start_verified_research_runtime(
            original_db=original,session_id=sid,
            output_dir=tmp_path/"unused",
            derived_receipt_db=original,
        )
    from infrastructure.spot_verified_research_runtime import (
        VerifiedResearchRuntimeError,
    )
    with pytest.raises(VerifiedResearchRuntimeError,match="independent"):
        asyncio.run(attempt())
    assert not (tmp_path/"unused").exists()
