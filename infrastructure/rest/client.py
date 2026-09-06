"""ECONITH :: infrastructure.rest.client

Thin async REST client skeleton for Binance (account, exchange info, historical
candles). Phase 0 provides signing/transport scaffolding only.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from config.environment import get_environment

logger = logging.getLogger("econith.infra.rest.client")


class BinanceRestClient:
    """Minimal signed REST client wrapper around httpx.AsyncClient."""

    def __init__(self) -> None:
        self._env = get_environment()
        self._client = httpx.AsyncClient(
            base_url=self._env.binance_rest_base_url, timeout=10.0
        )

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        params["timestamp"] = int(time.time() * 1000)
        query = urlencode(params)
        signature = hmac.new(
            self._env.effective_binance_trade_api_secret.encode(),
            query.encode(),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    async def get_public(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = await self._client.get(path, params=params or {})
        resp.raise_for_status()
        return resp.json()

    async def get_signed(self, path: str, params: dict[str, Any] | None = None) -> Any:
        headers = {"X-MBX-APIKEY": self._env.effective_binance_trade_api_key}
        signed = self._sign(dict(params or {}))
        resp = await self._client.get(path, params=signed, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def ping(self) -> bool:
        try:
            await self._client.get("/api/v3/ping")
            return True
        except httpx.HTTPError as exc:
            logger.warning("ping failed: %s", exc)
            return False

    async def close(self) -> None:
        await self._client.aclose()
