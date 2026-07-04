"""ECONITH :: core.observability.alerts

Lightweight webhook alert dispatcher for critical operational events.

Fires high-priority alerts to Discord and/or Telegram when a critical condition
occurs (``sentinel_freeze``, ``exchange_degraded``, ``websocket_disconnect``,
``db_failover``). Fully async, best-effort, and non-blocking: a failed webhook
never propagates into the caller. Throttled per event-key so a flapping
condition cannot spam the channel.

Configuration is read from the environment (kept optional so the platform runs
with alerting simply disabled when no webhook is set):

    ALERT_DISCORD_WEBHOOK   Discord incoming-webhook URL
    ALERT_TELEGRAM_TOKEN    Telegram bot token
    ALERT_TELEGRAM_CHAT_ID  Telegram chat id
    ALERT_MIN_INTERVAL_S    per-key throttle window (default 60s)
"""
from __future__ import annotations

import logging
import os
import time
from enum import Enum
from typing import Optional

logger = logging.getLogger("econith.core.observability.alerts")

__all__ = ["AlertSeverity", "AlertDispatcher", "get_alert_dispatcher"]


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


_SEVERITY_EMOJI = {
    AlertSeverity.INFO: "🟢",
    AlertSeverity.WARNING: "🟠",
    AlertSeverity.CRITICAL: "🔴",
}


class AlertDispatcher:
    """Async, throttled, best-effort webhook fan-out."""

    def __init__(
        self,
        *,
        discord_webhook: str = "",
        telegram_token: str = "",
        telegram_chat_id: str = "",
        min_interval_s: float = 60.0,
    ) -> None:
        self._discord = discord_webhook.strip()
        self._tg_token = telegram_token.strip()
        self._tg_chat = telegram_chat_id.strip()
        self._min_interval = max(0.0, min_interval_s)
        self._last_sent: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "AlertDispatcher":
        return cls(
            discord_webhook=os.getenv("ALERT_DISCORD_WEBHOOK", ""),
            telegram_token=os.getenv("ALERT_TELEGRAM_TOKEN", ""),
            telegram_chat_id=os.getenv("ALERT_TELEGRAM_CHAT_ID", ""),
            min_interval_s=float(os.getenv("ALERT_MIN_INTERVAL_S", "60") or 60),
        )

    @property
    def enabled(self) -> bool:
        return bool(self._discord or (self._tg_token and self._tg_chat))

    def _throttled(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_sent.get(key, 0.0)
        if now - last < self._min_interval:
            return True
        self._last_sent[key] = now
        return False

    async def dispatch(
        self,
        event_key: str,
        message: str,
        *,
        severity: AlertSeverity = AlertSeverity.CRITICAL,
        context: Optional[dict] = None,
    ) -> bool:
        """Fire an alert. Returns True if at least one channel accepted it.

        Never raises. Silently no-ops when alerting is disabled or throttled.
        """
        if not self.enabled:
            logger.debug("alert '%s' suppressed -- no webhook configured", event_key)
            return False
        if self._throttled(event_key):
            logger.debug("alert '%s' throttled", event_key)
            return False

        emoji = _SEVERITY_EMOJI.get(severity, "")
        ctx = ""
        if context:
            ctx = "\n" + "\n".join(f"• {k}: {v}" for k, v in context.items())
        text = f"{emoji} [ECONITH · {severity.value}] {event_key}\n{message}{ctx}"

        ok = False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                if self._discord:
                    ok = await self._send_discord(client, text) or ok
                if self._tg_token and self._tg_chat:
                    ok = await self._send_telegram(client, text) or ok
        except ImportError:
            logger.warning("httpx not installed -- cannot dispatch alert '%s'", event_key)
        except Exception as exc:  # noqa: BLE001 - alerting must never crash the caller
            logger.warning("alert dispatch failed for '%s': %s", event_key, exc)
        return ok

    async def _send_discord(self, client, text: str) -> bool:
        try:
            resp = await client.post(self._discord, json={"content": text})
            return 200 <= resp.status_code < 300
        except Exception as exc:  # noqa: BLE001
            logger.debug("discord alert failed: %s", exc)
            return False

    async def _send_telegram(self, client, text: str) -> bool:
        try:
            resp = await client.post(
                f"https://api.telegram.org/bot{self._tg_token}/sendMessage",
                json={"chat_id": self._tg_chat, "text": text},
            )
            return 200 <= resp.status_code < 300
        except Exception as exc:  # noqa: BLE001
            logger.debug("telegram alert failed: %s", exc)
            return False


_dispatcher: Optional[AlertDispatcher] = None


def get_alert_dispatcher() -> AlertDispatcher:
    """Cached, env-configured process-wide dispatcher."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = AlertDispatcher.from_env()
    return _dispatcher
