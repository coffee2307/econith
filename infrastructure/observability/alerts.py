"""ECONITH :: infrastructure.observability.alerts

Production Telegram alert dispatcher for operational events.

Design contract
---------------
* **Non-blocking**: ``schedule_*`` methods fire alerts via ``asyncio.create_task``
  so the trading/collector hot path never waits on network I/O.
* **Never raises**: every public method catches transport failures; a broken webhook
  cannot crash the runtime.
* **Throttled**: identical ``event_key`` values are deduplicated within
  ``ALERT_MIN_INTERVAL_S`` (default 60s) to survive flapping conditions.

Environment variables (first match wins for token/chat id)::

    TELEGRAM_BOT_API_TOKEN   primary Telegram bot token
    TELEGRAM_CHAT_ID         primary Telegram chat id
    ALERT_TELEGRAM_TOKEN     legacy alias for bot token
    ALERT_TELEGRAM_CHAT_ID   legacy alias for chat id
    ALERT_ENABLED            ``true``/``false`` (default true when token+chat set)
    ALERT_MIN_INTERVAL_S     per-key throttle window (default 60)
    ALERT_SERVICE_NAME       prefix shown in messages (default ECONITH)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from enum import Enum
from typing import Any, Optional

import httpx

from core.event_bus import Event, EventBus

logger = logging.getLogger("econith.infrastructure.observability.alerts")

__all__ = [
    "AlertSeverity",
    "AlertDispatcher",
    "get_alert_dispatcher",
    "register_runtime_alerts",
]

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_MAX_LEN = 4096

_SEVERITY_EMOJI = {
    "INFO": "🟢",
    "WARNING": "🟠",
    "CRITICAL": "🔴",
}


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


class AlertDispatcher:
    """Async, throttled, best-effort Telegram alert fan-out."""

    def __init__(
        self,
        *,
        bot_token: str = "",
        chat_id: str = "",
        enabled: bool = True,
        min_interval_s: float = 60.0,
        service_name: str = "ECONITH",
        timeout_s: float = 10.0,
    ) -> None:
        self._token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._enabled = enabled and bool(self._token and self._chat_id)
        self._min_interval = max(0.0, min_interval_s)
        self._service = service_name.strip() or "ECONITH"
        self._timeout = timeout_s
        self._last_sent: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

        if not self._enabled:
            logger.info("AlertDispatcher disabled (missing token/chat or ALERT_ENABLED=false)")
        else:
            logger.info("AlertDispatcher enabled for chat_id=%s", self._chat_id)

    @classmethod
    def from_env(cls) -> "AlertDispatcher":
        token = _first_env("TELEGRAM_BOT_API_TOKEN", "ALERT_TELEGRAM_TOKEN")
        chat_id = _first_env("TELEGRAM_CHAT_ID", "ALERT_TELEGRAM_CHAT_ID")
        has_creds = bool(token and chat_id)
        enabled = _env_bool("ALERT_ENABLED", has_creds)
        return cls(
            bot_token=token,
            chat_id=chat_id,
            enabled=enabled,
            min_interval_s=float(os.getenv("ALERT_MIN_INTERVAL_S", "60") or 60),
            service_name=os.getenv("ALERT_SERVICE_NAME", "ECONITH"),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def aclose(self) -> None:
        """Close the reusable HTTP client (call on shutdown)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- public async API -----------------------------------------------------
    async def send_info(
        self,
        event_key: str,
        message: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        return await self._send(AlertSeverity.INFO, event_key, message, context)

    async def send_warning(
        self,
        event_key: str,
        message: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        return await self._send(AlertSeverity.WARNING, event_key, message, context)

    async def send_critical(
        self,
        event_key: str,
        message: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        return await self._send(AlertSeverity.CRITICAL, event_key, message, context)

    # -- fire-and-forget (safe for hot paths) --------------------------------
    def schedule_info(
        self,
        event_key: str,
        message: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self._schedule(self.send_info(event_key, message, context=context))

    def schedule_warning(
        self,
        event_key: str,
        message: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self._schedule(self.send_warning(event_key, message, context=context))

    def schedule_critical(
        self,
        event_key: str,
        message: str,
        *,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        self._schedule(self.send_critical(event_key, message, context=context))

    # -- internals ------------------------------------------------------------
    def _schedule(self, coro: "asyncio.Future[bool] | asyncio.coroutines.Coroutine") -> None:
        task = asyncio.create_task(coro)
        task.add_done_callback(self._task_done)

    @staticmethod
    def _task_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("background alert task failed: %s", exc)

    def _throttled(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        if now - last < self._min_interval:
            return True
        self._last_sent[key] = now
        return False

    def _format_message(
        self,
        severity: AlertSeverity,
        event_key: str,
        message: str,
        context: Optional[dict[str, Any]],
    ) -> str:
        emoji = _SEVERITY_EMOJI.get(severity.value, "")
        lines = [
            f"{emoji} [{self._service} · {severity.value}] {event_key}",
            message,
        ]
        if context:
            lines.append("")
            lines.extend(f"• {k}: {v}" for k, v in context.items())
        text = "\n".join(lines)
        if len(text) > _TELEGRAM_MAX_LEN:
            return text[: _TELEGRAM_MAX_LEN - 3] + "..."
        return text

    async def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _send(
        self,
        severity: AlertSeverity,
        event_key: str,
        message: str,
        context: Optional[dict[str, Any]],
    ) -> bool:
        if not self._enabled:
            logger.debug("alert '%s' suppressed -- dispatcher disabled", event_key)
            return False
        if self._throttled(event_key):
            logger.debug("alert '%s' throttled", event_key)
            return False

        text = self._format_message(severity, event_key, message, context)
        url = _TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        try:
            client = await self._client_or_create()
            resp = await client.post(url, json=payload)
            if 200 <= resp.status_code < 300:
                logger.debug("alert '%s' delivered", event_key)
                return True
            logger.warning(
                "telegram alert '%s' rejected: HTTP %s %s",
                event_key,
                resp.status_code,
                resp.text[:200],
            )
        except Exception as exc:  # noqa: BLE001 - alerting must never crash caller
            logger.warning("telegram alert '%s' failed: %s", event_key, exc)
        return False


_dispatcher: AlertDispatcher | None = None


def get_alert_dispatcher() -> AlertDispatcher:
    """Cached, env-configured process-wide dispatcher."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher.from_env()
    return _dispatcher


# ---------------------------------------------------------------------------
# Runtime EventBus wiring (main.py)
# ---------------------------------------------------------------------------
async def _on_sentinel_emergency(alerts: AlertDispatcher, event: Event) -> None:
    payload = event.payload
    action = payload.get("action", "UNKNOWN")
    reason = payload.get("reason", "no reason provided")
    mode = payload.get("mode", "unknown")
    await alerts.send_critical(
        "sentinel_freeze",
        f"Sentinel emergency: {action}",
        context={"reason": reason, "mode": mode},
    )


async def _on_system_log(alerts: AlertDispatcher, event: Event) -> None:
    payload = event.payload
    source = str(payload.get("source", ""))
    level = str(payload.get("level", "")).lower()
    message = str(payload.get("message", ""))

    if source == "streamer" and "disconnected" in message.lower():
        await alerts.send_warning(
            "ws_disconnect",
            "Market data websocket disconnected",
            context={"detail": message},
        )
        return

    if source == "streamer" and level == "ok" and "connected" in message.lower():
        await alerts.send_info(
            "ws_reconnect",
            "Market data websocket reconnected",
            context={"detail": message},
        )


def register_runtime_alerts(bus: EventBus, alerts: AlertDispatcher | None = None) -> AlertDispatcher:
    """Subscribe alert handlers to critical runtime EventBus topics."""
    dispatcher = alerts or get_alert_dispatcher()

    async def sentinel_handler(event: Event) -> None:
        await _on_sentinel_emergency(dispatcher, event)

    async def log_handler(event: Event) -> None:
        await _on_system_log(dispatcher, event)

    bus.subscribe("sentinel.emergency", sentinel_handler)
    bus.subscribe("system.log", log_handler)
    logger.info("runtime alert handlers registered on EventBus")
    return dispatcher
