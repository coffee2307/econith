from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from ai.simulator_engine.macro_vectors import default_world
from ai.simulator_engine.world_kernel import WorldKernel
from core.event_bus import Event, EventBus
from core.system_controller import get_system_controller
from econith.world.sovereign.topology import ALL_CODES
from econith.world.agents.state_arrays import MicroConfig, MicroPopulation
from bridges.world_bridge import WorldBridge


def test_research_profile_preserves_ten_country_baseline() -> None:
    world = default_world()
    assert tuple(world.countries) == ("USA", "CHN", "VNM", "JPN", "IND", "DEU", "GBR", "FRA", "BRA", "SAU")
    assert all(world.tariff(a, b) == (0.0 if a == b else 0.03) for a in world.codes() for b in world.codes())
    assert world.to_dict() == default_world("research10").to_dict()
    with pytest.raises(ValueError):
        default_world("typo")


def test_seeded_population_is_repeatable_and_reports_actual_strata() -> None:
    a = MicroPopulation(MicroConfig(n_strata=8, seed=42))
    b = MicroPopulation(MicroConfig(n_strata=8, seed=42))
    assert a.n_strata == 8
    assert a.n_clusters == 1200
    assert a.country_snapshot("VNM") == b.country_snapshot("vnm")


def test_global_profile_materializes_all_150_countries() -> None:
    world = default_world(profile="global150")
    assert tuple(world.countries) == ALL_CODES
    assert len(world.countries) == 150
    assert all(country.feature_count_template() == 113 for country in world.countries.values())
    assert all(math.isfinite(v) for country in world.countries.values() for v in country.to_vector())
    assert world.to_dict() == default_world("global150").to_dict()


def test_world_exposes_6000_clusters_and_country_strata() -> None:
    kernel = WorldKernel(EventBus(), event_probability=0.0)
    population = kernel.state_dict()["agent_population"]["micro"]
    scale = kernel.state_dict()["scale"]
    detail = kernel.country_population_dict("VNM")
    assert population["n_clusters"] == 6000
    assert scale == {"countries": 150, "population_clusters": 6000, "strata_per_country": 40}
    assert detail is not None
    assert detail["n_strata"] == 40
    assert len(detail["strata"]) == 40


def test_150_country_tick_and_proxy_mutation() -> None:
    controller = get_system_controller()
    controller.set_world_simulation(True)
    kernel = WorldKernel(EventBus(), event_probability=0.0)
    result = asyncio.run(kernel.mutate_country("KHM", "monetary", "interest_rate", 0.08))
    assert kernel.country_dict("KHM")["interest_rate"] == 0.08
    asyncio.run(kernel._on_tick(Event("time.tick", {"sim_day": 1, "multiplier": 1})))
    assert result["ok"] is True
    assert len(kernel.state_dict()["countries"]) == 150
    assert math.isfinite(kernel.country_dict("KHM")["interest_rate"])


def test_invalid_mutations_do_not_change_state() -> None:
    kernel = WorldKernel(EventBus(), event_probability=0.0)
    before = kernel.world.to_dict()
    assert not asyncio.run(kernel.mutate_country("USA", "monetary", "interest_rate", float("nan")))["ok"]
    assert not asyncio.run(kernel.mutate_country("USA", "", "name", 1.0))["ok"]
    assert not asyncio.run(kernel.set_tariff("USA", "CHN", float("inf")))["ok"]
    assert kernel.world.to_dict() == before
    assert kernel.country_population_dict("INVALID") is None


def test_bridge_does_not_queue_rejected_mutations() -> None:
    kernel = SimpleNamespace(set_tariff=AsyncMock(return_value={"ok": False}), mutate_country=AsyncMock(return_value={"ok": False}))
    graph = SimpleNamespace(nodes={"USA": {}, "CHN": {}}, queue_tariff=Mock(), queue_mutation=Mock(), chronology=SimpleNamespace(active_id="test"))
    bridge = WorldBridge(kernel, graph)
    assert not asyncio.run(bridge.apply_tariff("USA", "CHN", -1))["sovereign"]["queued"]
    assert not asyncio.run(bridge.mutate("USA", "bad", "interest_rate", 0.1))["sovereign"]["queued"]
    graph.queue_tariff.assert_not_called()
    graph.queue_mutation.assert_not_called()


def test_bridge_keeps_accepted_non_graph_tariff_local() -> None:
    kernel = SimpleNamespace(set_tariff=AsyncMock(return_value={"ok": True}))
    graph = SimpleNamespace(nodes={"USA": {}}, queue_tariff=Mock(), chronology=SimpleNamespace(active_id="test"))
    result = asyncio.run(WorldBridge(kernel, graph).apply_tariff("USA", "KHM", 0.1))
    assert result["legacy"]["ok"]
    assert not result["sovereign"]["queued"]
    graph.queue_tariff.assert_not_called()
