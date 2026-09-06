"""ECONITH :: api.endpoints.control

Main System Control plane REST surface (Task 1 backend wiring).

Exposes the :class:`~core.system_controller.SystemController` to the dashboard:
operating-mode state machine, the CRITICAL compute guardrail (Enable World
Simulation), and the World->Quant bridge toggle. All mutating routes go through
the same API-key/audit contract as the rest of the platform.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config.settings import Settings, get_settings
from core.system_controller import OperatingMode, get_system_controller

__all__ = ["build_control_router"]


class OperatingModeRequest(BaseModel):
    mode: str  # one of OperatingMode


class ToggleRequest(BaseModel):
    enabled: bool


def _extract_credential(request: Request) -> Optional[str]:
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _require_api_key(request: Request, settings: Settings) -> None:
    if not settings.api_auth_enabled:
        return
    credential = _extract_credential(request)
    if credential is None or credential not in settings.api_keys:
        raise HTTPException(status_code=401, detail="valid API key required")


def build_control_router(
    *, api_prefix: str = "/api/v1", settings: Settings | None = None
) -> APIRouter:
    cfg = settings or get_settings()
    router = APIRouter()
    controller = get_system_controller()

    @router.get(f"{api_prefix}/control/state")
    async def control_state(request: Request) -> dict[str, Any]:
        state = controller.snapshot().as_dict()
        components = getattr(request.app.state, "components", {}) or {}
        predictor = components.get("predictor")
        if predictor is not None:
            state["agent_brain"] = getattr(predictor, "agent_brain", None)
            state["regime_brain"] = getattr(predictor, "regime_brain", None)
        hyp = components.get("hypothesis_runner")
        if hyp is not None:
            report = hyp.report  # type: ignore[union-attr]
            last = report.last_hypothesis
            last_run = report.runs[-1] if report.runs else None
            state["hypothesis"] = {
                "armed": hyp.is_armed(),  # type: ignore[union-attr]
                "total_ok": report.total_ok,
                "total_skipped": report.total_skipped,
                "total_error": report.total_error,
                "last_id": last.id if last else None,
                "last_prompt": last.prompt if last else None,
                "last_status": last_run.status if last_run else None,
            }
        macro = components.get("macro_hub")
        if macro is not None:
            snap = macro.snapshot()  # type: ignore[union-attr]
            state["macro_provenance"] = snap.get("source_provenance") or {}
        return state

    @router.get(f"{api_prefix}/control/modes")
    async def control_modes() -> dict[str, Any]:
        return {"modes": [m.value for m in OperatingMode]}

    @router.post(f"{api_prefix}/control/mode")
    async def set_operating_mode(req: OperatingModeRequest, request: Request) -> dict[str, Any]:
        _require_api_key(request, cfg)
        try:
            mode = OperatingMode(req.mode.strip().upper())
        except ValueError:
            raise HTTPException(status_code=422, detail=f"unknown mode {req.mode!r}")
        state = controller.set_mode(mode)
        # Re-authenticate the live CCXT session when the resolved gate is REALITY.
        components = getattr(request.app.state, "components", {}) or {}
        ccxt = components.get("ccxt_bridge")
        if ccxt is not None and state.quant_mode == "REALITY":
            await ccxt.connect()  # type: ignore[attr-defined]
        return state.as_dict()

    @router.post(f"{api_prefix}/control/world-simulation")
    async def set_world_simulation(req: ToggleRequest, request: Request) -> dict[str, Any]:
        """CRITICAL compute guardrail: OFF suspends the agent pipeline."""
        _require_api_key(request, cfg)
        return controller.set_world_simulation(req.enabled).as_dict()

    @router.post(f"{api_prefix}/control/world-bridge")
    async def set_world_bridge(req: ToggleRequest, request: Request) -> dict[str, Any]:
        _require_api_key(request, cfg)
        return controller.set_world_to_quant_bridge(req.enabled).as_dict()

    return router
