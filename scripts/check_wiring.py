"""ECONITH :: scripts.check_wiring

Phase-2 smoke test for vendor registry wiring in monitoring-only mode.

Checks:
1) Vendor registry initializes from on-disk manifest/SHA markers.
2) ``GET /api/v1/vendors/status`` responds and includes all mapped vendors.
3) Sentinel gate remains authoritative (FROZEN veto blocks ``order.intent``).
4) Mode gate remains enforced (REALITY blocks ``world.*`` into DOMAIN_QUANT).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.endpoints.vendors import build_vendors_router  # noqa: E402
from bridges.vendor_shims import build_default_registry  # noqa: E402
from config.settings import get_settings  # noqa: E402
from core.api import install_api_security  # noqa: E402
from core.event_bus import DOMAIN_QUANT, Event, EventBus  # noqa: E402
from core.mode import QuantMode, set_mode  # noqa: E402
from econith_quant.bridge.ai_bridge import AIBridge  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"[FAIL] {message}")
    print(f"[PASS] {message}")


async def _check_mode_gate() -> None:
    bus = EventBus()
    await bus.start()
    try:
        seen: list[Event] = []

        async def quant_handler(event: Event) -> None:
            seen.append(event)

        bus.subscribe("world.micro_impact", quant_handler, domain=DOMAIN_QUANT)
        set_mode(QuantMode.REALITY)
        await bus.publish("world.micro_impact", shock=1.0)
        await asyncio.sleep(0.05)
        _assert(not seen, "REALITY blocks world.* -> DOMAIN_QUANT")
    finally:
        set_mode(QuantMode.REALITY)
        await bus.stop()


async def _check_sentinel_gate() -> None:
    bus = EventBus()
    await bus.start()
    try:
        intents: list[Event] = []
        vetoes: list[Event] = []

        async def on_intent(event: Event) -> None:
            intents.append(event)

        async def on_veto(event: Event) -> None:
            vetoes.append(event)

        bus.subscribe("order.intent", on_intent)
        bus.subscribe("ai.veto", on_veto)
        bridge = AIBridge(bus)
        bridge.register()

        await bus.publish("sentinel.status", mode="FROZEN")
        await asyncio.sleep(0.01)
        await bus.publish("ai.signal", action="LONG", direction=1.0, confidence=0.8)
        await asyncio.sleep(0.05)
        _assert(not intents and bool(vetoes), "Sentinel FROZEN veto remains active")
    finally:
        await bus.stop()


async def _build_monitor_components() -> dict[str, Any]:
    bus = EventBus()
    registry = build_default_registry(bus)
    status = await registry.initialize()
    return {"vendor_registry": registry, "vendor_status": status}


def _check_vendor_endpoint(components: dict[str, Any]) -> None:
    settings = get_settings()
    app = FastAPI()
    install_api_security(app, settings)
    app.include_router(build_vendors_router(api_prefix=settings.api_prefix, settings=settings))
    app.state.components = components

    with TestClient(app) as client:
        headers = {}
        if settings.api_auth_enabled and settings.api_keys:
            token = sorted(settings.api_keys)[0]
            headers = {"x-api-key": token}
        res = client.get(f"{settings.api_prefix}/vendors/status", headers=headers)
        _assert(res.status_code == 200, "vendors status endpoint reachable")
        payload = res.json()
        _assert(isinstance(payload, dict), "vendors status payload is a JSON object")
        required = {
            "openbb", "qlib", "nofx", "trading_agents",
            "ai_hedge_fund", "zipline_reloaded", "mesa", "abides",
        }
        _assert(required.issubset(set(payload.keys())), "vendors status includes all 8 vendors")
        print(json.dumps(payload, indent=2))


async def main() -> int:
    print("== ECONITH Phase-2 Wiring Smoke Test ==")
    components = await _build_monitor_components()
    _check_vendor_endpoint(components)
    await _check_sentinel_gate()
    await _check_mode_gate()
    print("[PASS] wiring smoke test complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

