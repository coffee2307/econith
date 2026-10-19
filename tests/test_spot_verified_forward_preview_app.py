"""Explicit app-level forward preview must never start legacy or order paths."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from infrastructure.spot_verified_cutover_gate import (
    VerifiedIngressNotReady, enforce_verified_only_spot_ingress,
)
from infrastructure.spot_forward_verified_research import ForwardSpotResearchStatus


def _result(sid):
    return ForwardSpotResearchStatus(
        mode="SIMULATION",session_id=sid,original_records=6,
        verified_trades=3,forwarded_trades=2,forwarded_books=2,
        pre_sync_trades_not_forwarded=1,receiver_receipts=4,
        primary_derived_receipts=6,named_derived_receipts=12,
        book_final_update_id=103,all_receipts_independent=True,
        research_only=True,publication_in_session=True,
        session_complete=True,history_continuity_verified=False,
        continuous_live_market=False,execution_started=False,
        orders_submitted=0,
    )


def test_forward_preview_config_fails_before_any_app_startup(monkeypatch,tmp_path):
    out=tmp_path/"bounded"
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY","verified_forward_preview")
    monkeypatch.delenv("ECONITH_SPOT_FORWARD_PREVIEW_CONSENT",raising=False)
    monkeypatch.setenv("ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR",str(out))
    with pytest.raises(VerifiedIngressNotReady,match="consent"):
        enforce_verified_only_spot_ingress()
    monkeypatch.setenv(
        "ECONITH_SPOT_FORWARD_PREVIEW_CONSENT","PUBLIC_MARKET_DATA_RESEARCH_ONLY",
    )
    assert enforce_verified_only_spot_ingress()=="verified_forward_preview"
    out.mkdir()
    with pytest.raises(VerifiedIngressNotReady,match="new absolute"):
        enforce_verified_only_spot_ingress()


@pytest.mark.asyncio
async def test_forward_preview_app_isolated_and_never_exposes_legacy_routes(
    monkeypatch,tmp_path,
):
    output=(tmp_path/"preview").resolve()
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY","verified_forward_preview")
    monkeypatch.setenv(
        "ECONITH_SPOT_FORWARD_PREVIEW_CONSENT","PUBLIC_MARKET_DATA_RESEARCH_ONLY",
    )
    monkeypatch.setenv("ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR",str(output))
    import infrastructure.spot_verified_forward_preview_runtime as preview_module
    clock_calls=[]
    done=asyncio.Event()
    started=asyncio.Event()

    async def clock(*,max_abs_offset_ms,max_rtt_ms):
        assert max_abs_offset_ms==3000 and max_rtt_ms==2000
        clock_calls.append(1)
        return object()

    async def capture(**kwargs):
        assert kwargs["allow_public_network"] is True
        assert kwargs["source"]=="binance_public_simulation"
        started.set()
        await done.wait()
        return _result(kwargs["session_id"])

    monkeypatch.setattr(preview_module,"measure_public_clock",clock)
    monkeypatch.setattr(preview_module,"observe_forward_spot_research",capture)
    from main import app,components
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(started.wait(),timeout=3)
        assert clock_calls==[1]
        assert "verified_research_runtime" in components
        assert "engine" not in components
        assert "streamer" not in components
        assert "ccxt_bridge" not in components
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://isolated-research.local",
        ) as client:
            status=await client.get("/api/v1/market/verified/status")
            assert status.status_code==200
            data=status.json()
            assert data["status"]=="RUNNING"
            assert data["market_data_policy"]=="verified_forward_preview"
            assert data["orders_submitted"]==0
            assert data["continuous_live_market"] is False
            assert data["execution_started"] is False
            for method,path in (
                ("GET","/openapi.json"),("GET","/api/v1/health"),
                ("POST","/api/v1/mode"),("GET","/api/v1/metrics"),
                ("POST","/api/v1/quant/routing/profile"),
            ):
                response=await client.request(method,path)
                assert response.status_code==404,(path,response.status_code)
            done.set()
            await asyncio.wait_for(
                components["verified_research_runtime"]._task,timeout=3,
            )
            completed=(await client.get("/api/v1/market/verified/status")).json()
            assert completed["status"]=="COMPLETE_BOUNDED_RESEARCH"
            assert completed["result"]["receiver_receipts"]==4
            assert completed["result"]["orders_submitted"]==0
    assert not components
    assert app.state.verified_only_research is False


@pytest.mark.asyncio
async def test_clock_failure_rejects_preview_before_network_and_engine(monkeypatch,tmp_path):
    output=(tmp_path/"never").resolve()
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY","verified_forward_preview")
    monkeypatch.setenv(
        "ECONITH_SPOT_FORWARD_PREVIEW_CONSENT","PUBLIC_MARKET_DATA_RESEARCH_ONLY",
    )
    monkeypatch.setenv("ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR",str(output))
    import infrastructure.spot_verified_forward_preview_runtime as preview_module
    capture=[]
    async def refuse(**kwargs):
        raise RuntimeError("clock rejected public research admission")
    async def forbidden(**kwargs):
        capture.append(1)
        raise AssertionError("must not open network")
    monkeypatch.setattr(preview_module,"measure_public_clock",refuse)
    monkeypatch.setattr(preview_module,"observe_forward_spot_research",forbidden)
    from main import app,components
    with pytest.raises(RuntimeError,match="clock rejected"):
        async with app.router.lifespan_context(app):
            pytest.fail("unsafe clock got through")
    assert capture==[]
    assert not output.exists()
    assert "engine" not in components
    assert "streamer" not in components
    assert "ccxt_bridge" not in components
