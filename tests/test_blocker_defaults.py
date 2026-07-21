"""Defaults honesty: world off by default, macro provenance, attribution naming."""
from __future__ import annotations

import asyncio

import pytest


def test_world_simulation_default_false(monkeypatch) -> None:
    monkeypatch.setenv("WORLD_SIMULATION_DEFAULT", "false")
    from core.system_controller import SystemController

    ctrl = SystemController()
    assert ctrl.world_simulation_enabled is False
    snap = ctrl.snapshot()
    assert snap.autonomous_loop_implemented is False
    assert snap.autonomous_hypothesis_implemented is True


def test_world_simulation_default_true(monkeypatch) -> None:
    monkeypatch.setenv("WORLD_SIMULATION_DEFAULT", "true")
    from core.system_controller import SystemController

    ctrl = SystemController()
    assert ctrl.world_simulation_enabled is True


def test_macro_adapter_records_mock_provenance() -> None:
    from core.ingestion.adapters import FREDAdapter
    from core.ingestion.config import FREDConfig

    cfg = FREDConfig(FRED_API_KEY="", max_retries=0)
    adapter = FREDAdapter(cfg)

    async def _run():
        features = await adapter.collect()
        assert features
        assert adapter.last_provenance == "mock"
        assert adapter.last_provenance_reason

    asyncio.run(_run())


def test_attribution_json_declares_weighted_method() -> None:
    from ai.agents.base import AgentSignal
    from ai.explainability.attribution import attribution_to_json, build_attribution
    from ai.regime.switcher import Allocation

    signals = [
        AgentSignal("trend", 0.5, 0.8, contributions={"obi": 0.4, "volume_delta": 0.1}),
    ]
    alloc = Allocation(weights={"trend": 1.0}, regime="TRENDING")
    attr = build_attribution(signals, alloc)
    payload = attribution_to_json("LONG", 0.5, attr)
    assert payload["method"] == "weighted_feature_attribution"


def test_control_prefixes_include_control_routes() -> None:
    from config.settings import Settings

    prefixes = Settings().protected_path_prefixes
    assert any(p.endswith("/control/mode") for p in prefixes)
    assert any("world-simulation" in p for p in prefixes)
