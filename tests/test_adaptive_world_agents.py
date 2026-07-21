from __future__ import annotations

from ai.simulator_engine.adaptive_policy import ActionOption, AdaptivePolicy
from ai.simulator_engine.agents import TickBlackboard, default_intelligent_agents
from ai.simulator_engine.macro_vectors import default_world
from ai.simulator_engine.market_context import MarketSnapshot
from ai.simulator_engine.world_kernel import WorldKernel
from core.event_bus import EventBus
from econith.world.sovereign.agent_cluster import AgentClusterEngine


def _market(*, stress: float, regime: str, direction: float) -> MarketSnapshot:
    return MarketSnapshot(
        regime=regime,
        regime_confidence=0.9,
        ai_direction=direction,
        ai_confidence=0.8,
        ai_action="BUY" if direction >= 0 else "SELL",
        volatility=stress,
        sell_pressure=max(0.0, -direction),
        liquidation=stress * 0.5,
        stress=stress,
        funding_rate=0.0,
        oi_change_pct=0.0,
        sentinel_mode="NORMAL",
    )


def test_adaptive_policy_updates_selected_action_from_reward() -> None:
    policy = AdaptivePolicy(
        (ActionOption("wait", bias=-10.0), ActionOption("act", bias=10.0)),
        exploration=0.0,
        temperature=0.05,
        seed=3,
    )
    first = policy.decide("USA", features={}, utility=0.0)
    assert first.action == "act"
    policy.decide("USA", features={}, utility=0.8)
    assert policy.q_values("USA")["act"] > 0.0


def test_cluster_represents_million_agents_and_learns_actions() -> None:
    engine = AgentClusterEngine()
    for tick in range(30):
        engine.step(
            global_stress=0.7 if tick < 15 else 0.1,
            external_stress={"USA": 0.9 if tick < 15 else 0.0},
        )
    snapshot = engine.snapshot()
    assert snapshot["represented_agents"] == 1_000_000
    assert snapshot["n_clusters"] > 100
    assert abs(sum(snapshot["action_mix"].values()) - 1.0) < 0.01
    assert engine.country_signals("USA")["corporate_action"] in {
        "preserve",
        "invest",
        "adapt",
    }


def test_representative_agents_select_diverse_actions_across_regimes() -> None:
    world = default_world()
    agents = default_intelligent_agents()
    tags: set[str] = set()
    for market in (
        _market(stress=0.08, regime="CALM", direction=0.4),
        _market(stress=0.78, regime="VOLATILE", direction=-0.8),
    ):
        for _ in range(80):
            board = TickBlackboard(market=market)
            for code in world.codes():
                for agent in agents:
                    for fact in agent.evaluate(code, world, board).facts:
                        tags.update(fact.tags[:1])
    assert "corporate_expansion" in tags
    assert "capital_flight" in tags
    assert len(tags) >= 5


def test_world_kernel_hierarchy_tick_produces_injections() -> None:
    """One real tick must drive the hierarchy and leave a telemetry footprint."""
    import asyncio

    from core.event_bus import Event, EventBus
    from core.system_controller import get_system_controller
    from ai.simulator_engine.world_kernel import WorldKernel

    get_system_controller().set_world_simulation(True)
    kernel = WorldKernel(EventBus(), event_probability=0.0)
    asyncio.run(kernel._on_tick(Event("time.tick", {"sim_day": 1, "multiplier": 1})))
    assert kernel._last_broker_telemetry is not None
    assert kernel._last_broker_telemetry.tick >= 0
    assert kernel._broker.micro.n_clusters >= 5000
    # Micro impact should be available after at least one hierarchy step.
    assert kernel._last_micro is not None


def test_world_kernel_exposes_adaptive_population_state() -> None:
    kernel = WorldKernel(EventBus())
    population = kernel.state_dict()["agent_population"]
    assert population["micro"]["n_clusters"] >= 5000
    assert "llm_source" in population
    assert set(population["micro"]) >= {
        "tick", "n_clusters", "represented_agents", "mean_dissatisfaction", "hotspots",
    }


def test_sovereign_graph_honours_world_simulation_guardrail() -> None:
    import asyncio

    from ai.simulator_engine.sovereign_graph import AgentRole, PolicyProposal, default_world
    from core.engine import TickContext
    from core.event_bus import EventBus
    from core.system_controller import get_system_controller

    bus = EventBus()
    graph = default_world(bus)
    usa = graph.nodes["USA"].state
    before = usa.gdp_growth
    graph._tick_proposals = [
        PolicyProposal(
            role=AgentRole.GOVERNMENT,
            code="USA",
            field="gdp_growth",
            delta=0.05,
            reason="test",
        ),
    ]
    ctx = TickContext(sim_day=1, multiplier=1, tick_index=0, started_at=0.0)

    controller = get_system_controller()
    controller.set_world_simulation(True)
    asyncio.run(graph._phase_update(ctx))
    after_enabled = usa.gdp_growth

    controller.set_world_simulation(False)
    asyncio.run(graph._phase_update(ctx))
    after_disabled = usa.gdp_growth

    published: list[str] = []
    original_publish = bus.publish

    async def capture_publish(topic: str, **payload) -> None:
        published.append(topic)
        await original_publish(topic, **payload)

    bus.publish = capture_publish  # type: ignore[method-assign]
    asyncio.run(graph._phase_emit(ctx))
    controller.set_world_simulation(True)

    assert after_enabled != before
    assert after_disabled == after_enabled
    assert published == []
