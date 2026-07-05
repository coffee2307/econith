"""ECONITH VPS Collector :: alerts

Standalone Telegram alert dispatcher for the VPS Data Factory.

This module is intentionally self-contained (no imports from the main ECONITH
platform) so it can run on a lean Ubuntu VPS with only ``httpx`` added.

Environment variables::

    TELEGRAM_BOT_API_TOKEN   Telegram bot token
    TELEGRAM_CHAT_ID         Telegram chat id
    ALERT_ENABLED            true/false (default: true when token+chat set)
    ALERT_MIN_INTERVAL_S     per-key throttle window (default 60)
    ALERT_SERVICE_NAME       message prefix (default: ECONITH-VPS)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from enum import Enum
from typing import Any, Optional

import httpx

logger = logging.getLogger("econith.vps.alerts")

__all__ = ["AlertSeverity", "AlertDispatcher", "get_alert_dispatcher"]

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


class AlertDispatcher:
    """Async, throttled, best-effort Telegram alert fan-out."""

    def __init__(
        self,
        *,
        bot_token: str = "",
        chat_id: str = "",
        enabled: bool = True,
        min_interval_s: float = 60.0,
        service_name: str = "ECONITH-VPS",
        timeout_s: float = 10.0,
    ) -> None:
        self._token = bot_token.strip()
        self._chat_id = chat_id.strip()
        self._enabled = enabled and bool(self._token and self._chat_id)
        self._min_interval = max(0.0, min_interval_s)
        self._service = service_name.strip() or "ECONITH-VPS"
        self._timeout = timeout_s
        self._last_sent: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

        if not self._enabled:
            logger.info("AlertDispatcher disabled (missing token/chat or ALERT_ENABLED=false)")
        else:
            logger.info("AlertDispatcher enabled for chat_id=%s", self._chat_id)

    @classmethod
    def from_env(cls) -> "AlertDispatcher":
        token = os.getenv("TELEGRAM_BOT_API_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        has_creds = bool(token and chat_id)
        enabled = _env_bool("ALERT_ENABLED", has_creds)
        return cls(
            bot_token=token,
            chat_id=chat_id,
            enabled=enabled,
            min_interval_s=float(os.getenv("ALERT_MIN_INTERVAL_S", "60") or 60),
            service_name=os.getenv("ALERT_SERVICE_NAME", "ECONITH-VPS"),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

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

    def _schedule(self, coro) -> None:
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
        context: Optional[dict[str, Any]] = None,
    ) -> bool:
        if not self._enabled:
            return False
        if self._throttled(event_key):
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
                return True
            logger.warning(
                "telegram alert '%s' rejected: HTTP %s %s",
                event_key,
                resp.status_code,
                resp.text[:200],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram alert '%s' failed: %s", event_key, exc)
        return False


_dispatcher: AlertDispatcher | None = None


def get_alert_dispatcher() -> AlertDispatcher:
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher.from_env()
    return _dispatcher
