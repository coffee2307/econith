"""ECONITH :: core.observability.logging

Structured JSON logging.

Replaces the default stream formatter with a machine-parseable JSON schema so
logs ship cleanly to Loki/ELK/CloudWatch. Each line carries a stable schema:

    {
      "timestamp": ISO-8601 UTC,
      "log_level": "INFO",
      "component": "econith.sentinel.manager",
      "message": "...",
      "execution_routing": "DEGRADED" | null,
      "context_metadata": { ... arbitrary structured fields ... }
    }

Structured context is attached per-call via the ``extra={"context": {...}}``
convention, or ambiently via the :func:`log_context` context manager (thread-safe
via a contextvar) so a whole code region tags its logs without threading kwargs.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

# Ambient structured context merged into every record emitted within a region.
_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "econith_log_context", default={}
)

# Standard LogRecord attributes we never duplicate into context_metadata.
_RESERVED = frozenset(
    vars(logging.makeLogRecord({})).keys()
) | {"context", "execution_routing", "message", "asctime"}


class JsonLogFormatter(logging.Formatter):
    """Formats a LogRecord as a single structured JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        ambient = dict(_LOG_CONTEXT.get())
        # Per-call context via extra={"context": {...}}.
        call_ctx = getattr(record, "context", None)
        if isinstance(call_ctx, dict):
            ambient.update(call_ctx)
        # Any other non-reserved extras become context fields too.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                ambient.setdefault(key, value)

        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "log_level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
            "execution_routing": getattr(record, "execution_routing", None),
            "context_metadata": ambient or {},
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_json_logging(level: str | int = "INFO", *, force: bool = True) -> None:
    """Install the JSON formatter on the root logger's stream handler.

    Idempotent: replaces existing handlers when ``force`` so calling it in the
    ASGI lifespan startup cleanly upgrades uvicorn's default formatting.
    """
    root = logging.getLogger()
    lvl = logging.getLevelName(level) if isinstance(level, str) else level
    root.setLevel(lvl)
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Attach structured fields to every log emitted within the block.

    Nested contexts merge; the token restores the previous context on exit so it
    is safe across concurrent asyncio tasks (contextvars are task-local).
    """
    current = dict(_LOG_CONTEXT.get())
    current.update(fields)
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def bind_execution_routing(logger: logging.Logger, routing: Optional[str]) -> logging.LoggerAdapter:
    """Return an adapter that stamps ``execution_routing`` on every record."""
    return logging.LoggerAdapter(logger, {"execution_routing": routing})
