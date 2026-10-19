"""Privacy-preserving public Spot failure taxonomy for durable operator trials.

No exception messages or network payloads are persisted, only approved
static failure categories and bounded metrics. Never mark failed or uncertain
market history as accepted; diagnostics are for operator triage only.
"""
from __future__ import annotations

from infrastructure.spot_ingress_capture import SpotCaptureError
from infrastructure.spot_public_capture_adapter import SpotTransportError

_ALLOWED_TRANSPORT=frozenset({
    "QUEUE_BACKPRESSURE", "WS_EARLY_EOF", "WS_IDLE_TIMEOUT",
    "WS_READ_FAILED", "REST_SNAPSHOT_FAILED", "FRAME_LIMIT_UNSYNCED",
    "NO_REST_SNAPSHOT", "BOOK_NOT_SYNCED", "CAPTURE_TIMEOUT",
    "REST_HTTP_FAILED", "REST_BODY_OVERSIZE", "INJECTED_WS_DISCONNECT",
    "UNEXPECTED_STREAM", "TRANSPORT_ERROR",
    "CLOCK_PREFLIGHT_CONNECT_TIMEOUT",
})
_SPOOL_REASON={
    "projector source delivery lag exceeded":"FORWARD_PROJECTOR_SOURCE_LAG",
    "source spool pending capacity exceeded":"FORWARD_PROJECTOR_BACKLOG_CAPACITY",
    "durable original spool byte capacity exceeded":"FORWARD_SOURCE_SPOOL_BYTE_CAPACITY",
    "durable spool original cursor incomplete":"FORWARD_PROJECTOR_CURSOR_INCOMPLETE",
    "source spool original ordinal gap":"FORWARD_PROJECTOR_ORIGINAL_ORDER",
    "live spool refuses resumed source generation":"FORWARD_PROJECTOR_RESTART_REFUSED",
}
_CAPTURE_REASON={
    "source event is stale versus receipt":"SOURCE_EVENT_STALE",
    "source event is future-dated versus receipt":"SOURCE_EVENT_FUTURE",
    "trade event/trade clock lag exceeds tolerance":"SOURCE_TRADE_CLOCK_LAG",
    "capture capacity reached; no silent drops":"CAPTURE_STORAGE_CAPACITY",
    "backdated/out-of-order local receipt clock":"SOURCE_CLOCK_ROLLBACK",
    "session closed/reordered local clock":"SOURCE_CLOCK_ROLLBACK",
    "raw body size exceeds accepted bounds":"ORIGINAL_BYTES_OVERSIZE",
    "source record fingerprint mismatch":"SOURCE_HASH_MISMATCH",
    "source record bytes or time changed":"SOURCE_BYTES_TIME_CHANGED",
    "malformed original aggregate trade":"BAD_TRADE_EVENT",
    "wrong market or invalid Spot depth interval":"BAD_DEPTH_SEQUENCE",
}


def safe_failure_code(exc: Exception) -> str:
    """Never store raw exception texts; preserve only known source reason codes."""
    seen=set()
    current: BaseException | None=exc
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if type(current).__name__ == "ForwardSpotResearchError":
            code=_SPOOL_REASON.get(str(current))
            if code is not None:
                return code
        if isinstance(current,SpotTransportError):
            reason=current.reason
            if reason in _ALLOWED_TRANSPORT:
                return "SPOT_TRANSPORT_"+reason
            return "SPOT_TRANSPORT_UNCLASSIFIED"
        if isinstance(current,SpotCaptureError):
            detail=_CAPTURE_REASON.get(str(current))
            return "SPOT_CAPTURE_"+detail if detail else "SPOT_CAPTURE_UNCLASSIFIED"
        current=current.__cause__ if current.__cause__ is not None else current.__context__
    name=type(exc).__name__
    return name if len(name)<=70 and name.isidentifier() else "FORWARD_SOURCE_ERROR"


def failure_queue_peak(exc: Exception, *, max_capacity: int=128) -> int:
    """Return bounded failed-source peak only when capture directly observed it."""
    seen=set()
    current: BaseException | None=exc
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        raw=getattr(current,"ingress_queue_high_watermark",None)
        if type(raw) is int and 0 <= raw <= max_capacity:
            return raw
        current=current.__cause__ if current.__cause__ is not None else current.__context__
    return 0
