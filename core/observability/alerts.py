"""ECONITH :: core.observability.alerts

Backward-compatible re-exports. Canonical implementation lives in
``infrastructure.observability.alerts``.
"""
from infrastructure.observability.alerts import (
    AlertDispatcher,
    AlertSeverity,
    get_alert_dispatcher,
    register_runtime_alerts,
)

__all__ = [
    "AlertSeverity",
    "AlertDispatcher",
    "get_alert_dispatcher",
    "register_runtime_alerts",
]
