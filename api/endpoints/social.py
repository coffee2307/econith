"""ECONITH :: api.endpoints.social

First-party ``econith_social`` integration endpoints: health/status and an
optional reverse proxy into the Flask sidecar API.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from bridges.social_bridge import SocialServiceBridge
from config.settings import Settings, get_settings

__all__ = ["build_social_router"]

_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


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


def _bridge_from_request(request: Request) -> SocialServiceBridge:
    components = getattr(request.app.state, "components", {}) or {}
    bridge = components.get("social_bridge")
    if bridge is None:
        raise HTTPException(status_code=503, detail="econith_social bridge not initialized")
    return bridge


def build_social_router(
    *, api_prefix: str = "/api/v1", settings: Settings | None = None
) -> APIRouter:
    cfg = settings or get_settings()
    router = APIRouter(tags=["social"])

    @router.get(f"{api_prefix}/social/status")
    async def social_status(request: Request) -> dict[str, Any]:
        _require_api_key(request, cfg)
        bridge = _bridge_from_request(request)
        return await bridge.status(ui_url=cfg.social_ui_url)

    @router.api_route(
        f"{api_prefix}/social/proxy/{{path:path}}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )
    async def social_proxy(path: str, request: Request) -> Response:
        _require_api_key(request, cfg)
        bridge = _bridge_from_request(request)
        forward_headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
        }
        body = await request.body()
        try:
            upstream = await bridge.proxy(
                request.method,
                f"api/{path}",
                headers=forward_headers,
                content=body or None,
                params=dict(request.query_params),
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"econith_social upstream error: {exc}") from exc

        response_headers = {
            k: v
            for k, v in upstream.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
            media_type=upstream.headers.get("content-type"),
        )

    @router.get(f"{api_prefix}/social/health")
    async def social_health(request: Request) -> JSONResponse:
        """Unauthenticated liveness probe for compose / ops."""
        bridge = _bridge_from_request(request)
        payload = await bridge.health()
        code = 200 if payload.get("reachable") else 503
        return JSONResponse(payload, status_code=code)

    return router
