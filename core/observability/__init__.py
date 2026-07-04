"""ECONITH :: core.observability

Operational resilience primitives: structured JSON logging + a lightweight
webhook alert dispatcher for critical events.
"""
from __future__ import annotations

from core.observability.alerts import AlertDispatcher, AlertSeverity, get_alert_dispatcher
from core.observability.logging import (
    JsonLogFormatter,
    configure_json_logging,
    log_context,
)

__all__ = [
    "JsonLogFormatter",
    "configure_json_logging",
    "log_context",
    "AlertDispatcher",
    "AlertSeverity",
    "get_alert_dispatcher",
]
