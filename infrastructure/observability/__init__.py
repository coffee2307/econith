"""ECONITH :: infrastructure.observability

Production alerting and operational notification primitives.
"""
from infrastructure.observability.alerts import (
    AlertDispatcher,
    AlertSeverity,
    get_alert_dispatcher,
    register_runtime_alerts,
)

__all__ = [
    "AlertDispatcher",
    "AlertSeverity",
    "get_alert_dispatcher",
    "register_runtime_alerts",
]
