"""ECONITH :: bridges.social_bridge

HTTP bridge to the first-party ``econith_social`` sidecar (Flask + OASIS).
Used by ``backend_core`` for health probes and optional API proxying without
importing the social stack into the main EventBus runtime.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("econith.bridges.social")

__all__ = ["SocialServiceBridge", "social_source_present"]


def social_source_present(repo_root: Path | None = None) -> bool:
    """True when the in-tree ``econith_social`` backend entrypoint exists."""
    root = repo_root or Path(__file__).resolve().parents[1]
    return (root / "econith_social" / "backend" / "run.py").is_file()


class SocialServiceBridge:
    """Lightweight HTTP client for the econith_social Flask API."""

    def __init__(self, api_base_url: str) -> None:
        self._api_base = api_base_url.rstrip("/")

    @property
    def api_base_url(self) -> str:
        return self._api_base

    async def health(self) -> dict[str, Any]:
        source_present = social_source_present()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self._api_base}/health")
                body: Any = {}
                try:
                    body = response.json()
                except Exception:  # noqa: BLE001
                    body = {"raw": response.text[:200]}
                return {
                    "reachable": response.status_code == 200,
                    "source_present": source_present,
                    "status_code": response.status_code,
                    "body": body,
                }
        except Exception as exc:  # noqa: BLE001
            logger.debug("econith_social health probe failed: %s", exc)
            if not source_present:
                return {
                    "reachable": False,
                    "source_present": False,
                    "error": (
                        "econith_social sidecar unreachable — start it locally: "
                        "cd econith_social && npm run dev"
                    ),
                }
            return {
                "reachable": False,
                "source_present": True,
                "error": str(exc),
            }

    async def status(self, *, ui_url: str) -> dict[str, Any]:
        health = await self.health()
        return {
            "service": "econith_social",
            "pillar": "social",
            "api_base_url": self._api_base,
            "ui_url": ui_url,
            **health,
        }

    async def proxy(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Forward a request to the Flask sidecar."""
        url = f"{self._api_base}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.request(
                method.upper(),
                url,
                headers=headers,
                content=content,
                params=params,
            )
