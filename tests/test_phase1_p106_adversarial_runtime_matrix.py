"""P1-06 adversarial ASGI runtime surface tests for both opt-in policies.

These are isolated mocks: no Binance sockets, original SQLite replay, engine,
CCXT or OS trading. Not default runtime migration or a full security audit.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from infrastructure.spot_verified_cutover_gate import (
    VerifiedIngressNotReady, enforce_verified_only_spot_ingress,
)


@dataclass(frozen=True)
class Status:
    mode: str = "SIMULATION"
    market_data_policy: str = "isolated_research"
    status: str = "RUNNING"
    execution_started: bool = False
    orders_submitted: int = 0
    continuous_live_market: bool = False


class OfflineStub:
    status = Status()

    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def configure(monkeypatch, tmp_path, kind):
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY", kind)
    if kind == "verified_only":
        path = (tmp_path / "sealed_original.db").resolve()
        path.write_bytes(b"fixture-only; runtime start is mocked")
        monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_DB", str(path))
        monkeypatch.setenv("ECONITH_SPOT_VERIFIED_REPLAY_SESSION", "rec_mock_only")
        monkeypatch.setenv(
            "ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR",
            str((tmp_path / "output_replay").resolve()),
        )
    else:
        monkeypatch.setenv(
            "ECONITH_SPOT_FORWARD_PREVIEW_CONSENT", "PUBLIC_MARKET_DATA_RESEARCH_ONLY"
        )
        monkeypatch.setenv(
            "ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR",
            str((tmp_path / "output_preview").resolve()),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["verified_only", "verified_forward_preview"])
async def test_only_single_get_status_allowed_no_mutators_or_legacy_components(
    monkeypatch, tmp_path, kind,
):
    configure(monkeypatch, tmp_path, kind)
    assert enforce_verified_only_spot_ingress() == kind
    import main

    if kind == "verified_only":
        import infrastructure.spot_verified_research_runtime as adapter
        method = "start_verified_research_runtime"
    else:
        import infrastructure.spot_verified_forward_preview_runtime as adapter
        method = "start_verified_forward_preview"
    mock = OfflineStub()
    invoked = []

    async def start(**kw):
        invoked.append(kw)
        return mock

    monkeypatch.setattr(adapter, method, start)
    assert not main.components
    async with main.app.router.lifespan_context(main.app):
        assert len(invoked) == 1
        assert main.app.state.verified_only_research is True
        assert main.components.get("verified_research_runtime") is mock
        for blocked in (
            "engine", "streamer", "world", "ccxt_bridge",
            "quant_exec", "execution_bridge", "predictor",
        ):
            assert blocked not in main.components
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://untrusted.invalid",
            follow_redirects=False,
        ) as http:
            for path in (
                "/api/v1/market/verified/status",
                "/api/v1/market/verified/status?mutate=true",
            ):
                response = await http.get(path)
                assert response.status_code == 200
                body = response.json()
                assert body["mode"] == "SIMULATION"
                assert body["execution_started"] is False
                assert body["orders_submitted"] == 0
            for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
                rsp = await http.request(
                    method, "/api/v1/market/verified/status",
                    content=b'{"mode":"REALITY"}',
                )
                assert rsp.status_code == 404, (kind, method, rsp.status_code)
            denied = [
                "/api/v1/mode", "/api/v1/quant/status",
                "/api/v1/market/verified/status/",
                "/api/v1/market/verified/status/more",
                "/api/v1/world/state", "/api/v1/health",
                "/api/v1/control/state", "/api/v1/runtime/resume",
                "/api/v1/quant/routing/profile", "/api/v1/metrics",
                "/openapi.json", "/docs", "/redoc", "/", "/favicon.ico",
            ]
            for path in denied:
                response = await http.get(path)
                assert response.status_code == 404, (kind, path, response.status_code)
                response = await http.post(path, json={"mode": "REALITY"})
                assert response.status_code == 404, (kind, path, response.status_code)
    assert mock.closed is True
    assert not main.components
    assert main.app.state.verified_only_research is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["verified_only", "verified_forward_preview"])
async def test_startup_fault_propagates_without_unverified_fallback(
    monkeypatch, tmp_path, kind,
):
    configure(monkeypatch, tmp_path, kind)
    import main

    if kind == "verified_only":
        import infrastructure.spot_verified_research_runtime as adapter
        method = "start_verified_research_runtime"
    else:
        import infrastructure.spot_verified_forward_preview_runtime as adapter
        method = "start_verified_forward_preview"

    async def fail_start(**kw):
        raise RuntimeError("INJECTED_RESEARCH_START_FAILURE")

    monkeypatch.setattr(adapter, method, fail_start)
    with pytest.raises(RuntimeError, match="INJECTED_RESEARCH_START_FAILURE"):
        async with main.app.router.lifespan_context(main.app):
            pytest.fail("a failed research source must never start legacy backend")
    assert not main.components
    assert getattr(main.app.state, "verified_only_research", False) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["verified_only", "verified_forward_preview"])
async def test_shutdown_exception_always_clears_protected_runtime_components(
    monkeypatch, tmp_path, kind,
):
    configure(monkeypatch, tmp_path, kind)
    import main

    if kind == "verified_only":
        import infrastructure.spot_verified_research_runtime as adapter
        method = "start_verified_research_runtime"
    else:
        import infrastructure.spot_verified_forward_preview_runtime as adapter
        method = "start_verified_forward_preview"

    class BrokenClose(OfflineStub):
        async def close(self):
            self.closed = True
            raise RuntimeError("INJECTED_RESEARCH_CLOSE_FAILURE")

    obj = BrokenClose()

    async def start(**kwargs):
        return obj

    monkeypatch.setattr(adapter, method, start)
    with pytest.raises(RuntimeError, match="INJECTED_RESEARCH_CLOSE_FAILURE"):
        async with main.app.router.lifespan_context(main.app):
            assert main.app.state.verified_only_research is True
            assert main.components["verified_research_runtime"] is obj
    assert obj.closed is True
    assert main.components == {}
    assert main.app.state.verified_only_research is False


@pytest.mark.parametrize("kind", ["verified_only", "verified_forward_preview"])
def test_missing_policy_consent_never_falls_back_to_legacy(
    monkeypatch, tmp_path, kind,
):
    monkeypatch.setenv("ECONITH_SPOT_INGRESS_POLICY", kind)
    monkeypatch.delenv("ECONITH_SPOT_VERIFIED_REPLAY_DB", raising=False)
    monkeypatch.delenv("ECONITH_SPOT_VERIFIED_REPLAY_SESSION", raising=False)
    monkeypatch.delenv("ECONITH_SPOT_VERIFIED_REPLAY_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("ECONITH_SPOT_FORWARD_PREVIEW_CONSENT", raising=False)
    monkeypatch.delenv("ECONITH_SPOT_FORWARD_PREVIEW_OUTPUT_DIR", raising=False)
    with pytest.raises(VerifiedIngressNotReady):
        enforce_verified_only_spot_ingress()
    assert not (tmp_path / "capture").exists()
