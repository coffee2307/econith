"""ECONITH :: core.api

HTTP-layer cross-cutting concerns (authentication, audit trail).
"""
from __future__ import annotations

from core.api.auth import (
    APIKeyAuthMiddleware,
    AuditTrailLogger,
    get_audit_logger,
    install_api_security,
)

__all__ = [
    "APIKeyAuthMiddleware",
    "AuditTrailLogger",
    "get_audit_logger",
    "install_api_security",
]
