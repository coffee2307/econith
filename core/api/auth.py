"""ECONITH :: core.api.auth

Guarded API security + operational audit trail.

A rigid, low-overhead authentication middleware that inspects an API key or
Bearer token on sensitive *mutating* router paths (mode switching, world
mutation, execution-intent injection, sentinel controls). Read-only endpoints
and the websocket streams are never gated so the dashboard keeps working.

Every state-altering command that reaches a protected route is written to a
rotating, structured audit log (JSON lines) so operator actions are fully
traceable. API keys are never logged in the clear -- only a short SHA-256
fingerprint is recorded.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from config.settings import Settings, get_settings

logger = logging.getLogger("econith.core.api.auth")

# HTTP methods considered state-mutating; only these are gated on protected paths.
_MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------
class AuditTrailLogger:
    """Structured, rotating audit sink for operator state-mutation commands."""

    def __init__(
        self,
        path: str,
        *,
        max_bytes: int = 5_000_000,
        backups: int = 5,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger("econith.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        if not any(
            isinstance(h, RotatingFileHandler)
            and getattr(h, "baseFilename", "") == str(self._path.resolve())
            for h in self._logger.handlers
        ):
            handler = RotatingFileHandler(
                self._path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

    def record(
        self,
        *,
        method: str,
        path: str,
        query: str,
        client: str,
        decision: str,
        key_fingerprint: Optional[str],
        status_code: Optional[int] = None,
    ) -> None:
        """Append one structured audit line describing an operator command."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "path": path,
            "query": query or None,
            "client": client,
            "decision": decision,
            "key_fp": key_fingerprint,
            "status": status_code,
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)
        self._logger.info(line)
        # Mirror denials to the app logger for immediate operator visibility.
        if decision != "ALLOW":
            logger.warning("[AUDIT] %s", line)


_audit_logger: Optional[AuditTrailLogger] = None


def get_audit_logger(settings: Optional[Settings] = None) -> AuditTrailLogger:
    """Cached accessor for the process-wide audit trail sink."""
    global _audit_logger
    if _audit_logger is None:
        cfg = settings or get_settings()
        _audit_logger = AuditTrailLogger(
            cfg.audit_log_path,
            max_bytes=cfg.audit_log_max_bytes,
            backups=cfg.audit_log_backups,
        )
    return _audit_logger


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------
def _fingerprint(token: str) -> str:
    """Short, non-reversible fingerprint of a credential for safe logging."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _extract_credential(request: Request) -> Optional[str]:
    """Pull a credential from ``X-API-Key`` or ``Authorization: Bearer <token>``."""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Gate sensitive mutating routes behind an API key / bearer token."""

    def __init__(self, app: ASGIApp, settings: Optional[Settings] = None) -> None:
        super().__init__(app)
        self._settings = settings or get_settings()
        self._audit = get_audit_logger(self._settings)
        self._protected = self._settings.protected_path_prefixes
        if not self._settings.api_auth_enabled:
            logger.warning(
                "API auth DISABLED -- sensitive routes are open. Set "
                "API_AUTH_ENABLED=true and API_KEYS to enforce."
            )
        elif not self._settings.api_keys:
            logger.error(
                "API_AUTH_ENABLED=true but API_KEYS is empty -- all protected "
                "routes will reject every request."
            )

    def _is_protected(self, method: str, path: str) -> bool:
        if method.upper() not in _MUTATING_METHODS:
            return False
        return any(path.startswith(prefix) for prefix in self._protected)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        method = request.method

        if not self._is_protected(method, path):
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        query = request.url.query
        credential = _extract_credential(request)
        fingerprint = _fingerprint(credential) if credential else None

        # Auth disabled: allow but audit every mutation (dev/insecure posture).
        if not self._settings.api_auth_enabled:
            self._audit.record(
                method=method, path=path, query=query, client=client,
                decision="ALLOW_AUTH_DISABLED", key_fingerprint=fingerprint,
            )
            return await call_next(request)

        # Auth enabled: require a recognised credential.
        if credential is None or credential not in self._settings.api_keys:
            self._audit.record(
                method=method, path=path, query=query, client=client,
                decision="DENY", key_fingerprint=fingerprint, status_code=401,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "detail": "valid API key required for this operation",
                },
            )

        response = await call_next(request)
        self._audit.record(
            method=method, path=path, query=query, client=client,
            decision="ALLOW", key_fingerprint=fingerprint,
            status_code=response.status_code,
        )
        return response


def install_api_security(app: ASGIApp, settings: Optional[Settings] = None) -> None:
    """Attach the API-key auth + audit middleware to a FastAPI/Starlette app.

    ``app`` must expose ``add_middleware`` (FastAPI). Kept as a thin helper so
    ``main.py`` wires security in one line.
    """
    cfg = settings or get_settings()
    app.add_middleware(APIKeyAuthMiddleware, settings=cfg)  # type: ignore[attr-defined]
    logger.info(
        "API security installed (auth_enabled=%s, protected_prefixes=%d)",
        cfg.api_auth_enabled, len(cfg.protected_path_prefixes),
    )
