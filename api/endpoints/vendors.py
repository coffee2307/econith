"""ECONITH :: api.endpoints.vendors

Monitoring-only vendor observability endpoints.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from config.settings import Settings, get_settings
from core.api.auth import require_api_key as _require_api_key

__all__ = ["build_vendors_router"]


def build_vendors_router(
    *, api_prefix: str = "/api/v1", settings: Settings | None = None
) -> APIRouter:
    cfg = settings or get_settings()
    router = APIRouter()

    @router.get(f"{api_prefix}/vendors/status")
    async def vendors_status(request: Request) -> dict[str, Any]:
        _require_api_key(request, cfg)
        components = getattr(request.app.state, "components", {}) or {}
        registry = components.get("vendor_registry")
        raw = registry.status() if registry is not None else {}
        out: dict[str, dict[str, Any]] = {}
        for name, payload in raw.items():
            out[name] = {
                "status": payload.get("status", "MISSING"),
                "pillar": payload.get("pillar"),
                "consumes": list(payload.get("consumes", [])),
                "emits": list(payload.get("emits", [])),
                "simulation_only": bool(payload.get("simulation_only", False)),
            }
        return out

    return router

