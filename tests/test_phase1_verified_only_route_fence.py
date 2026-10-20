"""Runtime ASGI attack-surface matrix for opt-in verified research isolation.

Every HTTP path other than the single GET status must be 404, including
routes registered at import time, docs/OpenAPI, health, quant mode and
operator actions. This test opens no market socket, trading connection or DB.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest


@dataclass(frozen=True)
class StubStatus:
    mode:str="SIMULATION"
    market_data_policy:str="verified_forward_preview"
    status:str="RUNNING"
    execution_started:bool=False
    orders_submitted:int=0
    continuous_live_market:bool=False


class ClosedResearchStub:
    status=StubStatus()
    def __init__(self):
        self.closed=False
    async def close(self):
        self.closed=True


@pytest.mark.asyncio
async def test_research_runtime_http_method_and_route_allowlist_fail_closed(
    monkeypatch,tmp_path,
):
    monkeypatch.setenv("ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR",str((tmp_path/"guarded").resolve()))
    monkeypatch.setenv("ECONITH_SPOT_FORWARD_PREVIEW_CONSENT","PUBLIC_MARKET_DATA_RESEARCH_ONLY")
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY","verified_forward_preview")
    import main
    import infrastructure.spot_verified_forward_preview_runtime as module
    observations=[]
    stub=ClosedResearchStub()

    async def start(**kwargs):
        observations.append(kwargs)
        return stub
    monkeypatch.setattr(module,"start_verified_forward_preview",start)
    # Use the real protected policy gate, not a mocked policy. The mocked
    # runtime ensures this test cannot contact public Binance or place orders.
    async with main.app.router.lifespan_context(main.app):
        assert main.app.state.verified_only_research is True
        assert len(observations)==1
        assert observations[0]["output_dir"]==(tmp_path/"guarded").resolve()
        assert "verified_research_runtime" in main.components
        for key in ("engine","streamer","ccxt_bridge","execution_bridge","world"):
            assert key not in main.components
        transport=httpx.ASGITransport(app=main.app)
        async with httpx.AsyncClient(
            transport=transport,base_url="http://verified-gate.invalid"
        ) as http:
            allowed=await http.get("/api/v1/market/verified/status")
            assert allowed.status_code==200,allowed.text
            assert allowed.json()["orders_submitted"]==0
            assert allowed.json()["execution_started"] is False
            # Identical path with wrong method, query tricks and accidental
            # trailing slash MUST NOT reach the pre-registered legacy routes.
            blocked=[
                ("POST","/api/v1/market/verified/status"),
                ("PUT","/api/v1/market/verified/status"),
                ("PATCH","/api/v1/market/verified/status"),
                ("DELETE","/api/v1/market/verified/status"),
                ("HEAD","/api/v1/market/verified/status"),
                ("OPTIONS","/api/v1/market/verified/status"),
                ("GET","/api/v1/market/verified/status/"),
                ("GET","/api/v1/health"),
                ("GET","/api/v1/metrics"),
                ("GET","/api/v1/quant/status"),
                ("POST","/api/v1/quant/routing/profile"),
                ("POST","/api/v1/mode"),
                ("POST","/api/v1/market/verified/status/foo"),
                ("POST","/api/v1/world/mutate"),
                ("POST","/api/v1/inject"),
                ("GET","/openapi.json"),
                ("GET","/docs"),
                ("GET","/redoc"),
                ("GET","/"),
            ]
            for method,path in blocked:
                response=await http.request(method,path)
                assert response.status_code==404,(method,path,response.status_code,response.text[:100])
    assert stub.closed is True
    assert not main.components
    assert main.app.state.verified_only_research is False
