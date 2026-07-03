"""ECONITH :: ai.simulator_engine.sovereign_graph

Sovereign Multi-Agent Butterfly-Effect graph.

Models the global economy as a stateful graph of sovereign nation-states, each a
micro-cosm of four self-interested, goal-driven agents that interact within the
CORE's deterministic 5-phase Tick Engine:

    GovernmentAgent   -- fiscal policy, tax rates, cross-border tariffs
    CentralBankAgent  -- Taylor-rule monetary policy, rates, FX pegs
    EnterpriseAgent   -- profit-maximising firm; relocates supply chains
    PublicAgent       -- consumer confidence, wage-inflation tolerance, unrest

Nations are bound by a :class:`GlobalTradeMatrix`. A structural mutation (e.g.
"USA imposes a 50% tariff on China") propagates as a causal chain across ticks,
forking a stateful :class:`ScenarioChronology` so every future reaction is bound
to the emergent past.

This module is complementary to the existing market-aware ensemble in
``agents.py``: it is the *macro* sovereign layer, expressed as a clean abstract
network model that plugs directly into ``core.engine.TickPipeline`` phase hooks.
"""
from __future__ import annotations

import itertools
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.engine import TickContext, TickPhase, TickPipeline
from core.event_bus import EventBus

logger = logging.getLogger("econith.world.sovereign_graph")

__all__ = [
    "AgentRole",
    "SovereignState",
    "PolicyProposal",
    "GlobalTradeMatrix",
    "SovereignNode",
    "SovereignAgent",
    "GovernmentAgent",
    "CentralBankAgent",
    "EnterpriseAgent",
    "PublicAgent",
    "ScenarioNode",
    "ScenarioChronology",
    "SovereignWorldGraph",
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class AgentRole(str, Enum):
    GOVERNMENT = "government"
    CENTRAL_BANK = "central_bank"
    ENTERPRISE = "enterprise"
    PUBLIC = "public"


# ---------------------------------------------------------------------------
# Sovereign state
# ---------------------------------------------------------------------------
@dataclass
class SovereignState:
    """Mutable macro state for one nation/bloc."""

    code: str
    name: str
    gdp: float                       # nominal GDP (USD)
    gdp_growth: float = 0.025
    inflation: float = 0.03
    policy_rate: float = 0.03
    unemployment: float = 0.05
    corporate_tax: float = 0.21
    income_tax: float = 0.24
    fx_rate: float = 1.0             # units of local currency per USD
    fx_peg: float | None = None      # target peg, if any
    foreign_reserves: float = 0.0
    export_index: float = 100.0
    supply_chain_friction: float = 0.05
    consumer_confidence: float = 0.6
    wage_inflation_tolerance: float = 0.5
    social_unrest: float = 0.1
    political_stability: float = 0.8

    def apply(self, field_name: str, delta: float) -> None:
        current = getattr(self, field_name)
        setattr(self, field_name, current + delta)

    def snapshot(self) -> dict[str, Any]:
        return {
            "code": self.code, "name": self.name, "gdp": self.gdp,
            "gdp_growth": round(self.gdp_growth, 5),
            "inflation": round(self.inflation, 5),
            "policy_rate": round(self.policy_rate, 5),
            "unemployment": round(self.unemployment, 5),
            "corporate_tax": round(self.corporate_tax, 4),
            "fx_rate": round(self.fx_rate, 5),
            "export_index": round(self.export_index, 3),
            "supply_chain_friction": round(self.supply_chain_friction, 4),
            "consumer_confidence": round(self.consumer_confidence, 4),
            "social_unrest": round(self.social_unrest, 4),
            "political_stability": round(self.political_stability, 4),
        }


@dataclass(slots=True)
class PolicyProposal:
    """A single mutation an agent wants applied during PHASE 4 (UPDATE_WORLD)."""

    role: AgentRole
    code: str
    field: str
    delta: float
    reason: str
    #: optional (source, target) tariff edge mutation
    tariff_edge: tuple[str, str] | None = None
    narrative: str = ""


# ---------------------------------------------------------------------------
# Global trade matrix (the graph edges)
# ---------------------------------------------------------------------------
class GlobalTradeMatrix:
    """Directed weighted trade graph plus a tariff overlay.

    ``flow[(a, b)]`` is the baseline export intensity from ``a`` to ``b``.
    ``tariff[(a, b)]`` is the tariff ``a`` imposes on imports from ``b``.
    """

    def __init__(self) -> None:
        self._flow: dict[tuple[str, str], float] = {}
        self._tariff: dict[tuple[str, str], float] = {}

    def set_flow(self, source: str, target: str, weight: float) -> None:
        self._flow[(source, target)] = _clamp(weight, 0.0, 1.0)

    def flow(self, source: str, target: str) -> float:
        return self._flow.get((source, target), 0.0)

    def set_tariff(self, source: str, target: str, rate: float) -> float:
        self._tariff[(source, target)] = _clamp(rate, 0.0, 1.0)
        return self._tariff[(source, target)]

    def tariff(self, source: str, target: str) -> float:
        return self._tariff.get((source, target), 0.0)

    def reroute(self, blocked: str, alternative: str, target: str, fraction: float) -> None:
        """Divert a fraction of ``blocked``'s exports-to-``target`` onto
        ``alternative`` -- the mechanical heart of the butterfly effect."""
        diverted = self.flow(blocked, target) * fraction
        self._flow[(blocked, target)] = self.flow(blocked, target) - diverted
        self._flow[(alternative, target)] = self.flow(alternative, target) + diverted

    def snapshot(self) -> dict[str, Any]:
        return {
            "flows": {f"{a}->{b}": round(w, 4) for (a, b), w in self._flow.items()},
            "tariffs": {f"{a}->{b}": round(r, 4) for (a, b), r in self._tariff.items()},
        }


# ---------------------------------------------------------------------------
# Sovereign node (a nation as a micro-cosm of 4 agents)
# ---------------------------------------------------------------------------
class SovereignNode:
    """A nation-state: its :class:`SovereignState` plus its four internal agents."""

    def __init__(self, state: SovereignState) -> None:
        self.state = state
        self.agents: list[SovereignAgent] = [
            GovernmentAgent(state.code),
            CentralBankAgent(state.code),
            EnterpriseAgent(state.code),
            PublicAgent(state.code),
        ]

    def deliberate(
        self, world: "SovereignWorldGraph", ctx: TickContext
    ) -> list[PolicyProposal]:
        proposals: list[PolicyProposal] = []
        for agent in sorted(self.agents, key=lambda a: a.priority):
            proposals.extend(agent.evaluate(self.state, world, ctx))
        return proposals


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------
class SovereignAgent(ABC):
    """Abstract goal-driven agent inside one sovereign node."""

    role: AgentRole
    priority: int = 0

    def __init__(self, code: str) -> None:
        self.code = code

    @abstractmethod
    def evaluate(
        self, state: SovereignState, world: "SovereignWorldGraph", ctx: TickContext
    ) -> list[PolicyProposal]:
        raise NotImplementedError


class GovernmentAgent(SovereignAgent):
    """Fiscal sovereign: taxes, subsidies and retaliatory tariffs."""

    role = AgentRole.GOVERNMENT
    priority = 10

    def evaluate(self, state, world, ctx):
        proposals: list[PolicyProposal] = []
        # Defend domestic inflation: if inflation runs hot AND a rival has
        # tariffed us, retaliate to protect terms of trade.
        rival, incoming = world.worst_incoming_tariff(state.code)
        if incoming > 0.1:
            retaliation = _clamp(incoming * 0.6, 0.0, 0.5)
            proposals.append(PolicyProposal(
                role=self.role, code=state.code, field="export_index",
                delta=-2.0 * incoming, reason="tariff_shock",
                tariff_edge=(state.code, rival) if rival else None,
                narrative=(f"{state.name} government retaliates against {rival} "
                           f"with a {retaliation*100:.0f}% counter-tariff"),
            ))
        # Counter-cyclical fiscal loosening when growth stalls.
        if state.gdp_growth < 0.01:
            proposals.append(PolicyProposal(
                role=self.role, code=state.code, field="corporate_tax",
                delta=-0.01, reason="fiscal_stimulus",
                narrative=f"{state.name} cuts corporate tax to arrest the slowdown",
            ))
        return proposals


class CentralBankAgent(SovereignAgent):
    """Monetary sovereign implementing a Taylor-rule adaptation + FX defence."""

    role = AgentRole.CENTRAL_BANK
    priority = 20

    #: Taylor-rule coefficients
    _NEUTRAL_RATE = 0.02
    _INFLATION_TARGET = 0.02
    _PHI_PI = 0.5
    _PHI_Y = 0.5

    def evaluate(self, state, world, ctx):
        proposals: list[PolicyProposal] = []
        output_gap = state.gdp_growth - 0.025
        taylor = (
            self._NEUTRAL_RATE
            + state.inflation
            + self._PHI_PI * (state.inflation - self._INFLATION_TARGET)
            + self._PHI_Y * output_gap
        )
        rate_delta = _clamp((taylor - state.policy_rate) * 0.3, -0.01, 0.01)
        if abs(rate_delta) > 1e-4:
            proposals.append(PolicyProposal(
                role=self.role, code=state.code, field="policy_rate",
                delta=rate_delta, reason="taylor_rule",
                narrative=(f"{state.name} central bank "
                           f"{'hikes' if rate_delta > 0 else 'cuts'} "
                           f"rates by {abs(rate_delta)*1e4:.0f}bps"),
            ))
        # Defend an FX peg / competitiveness: devalue to sustain exports if
        # export index has cratered under a tariff shock.
        if state.export_index < 95.0 and state.fx_peg is None:
            proposals.append(PolicyProposal(
                role=self.role, code=state.code, field="fx_rate",
                delta=state.fx_rate * 0.01, reason="competitive_devaluation",
                narrative=f"{state.name} lets its currency slide to defend exports",
            ))
        return proposals


class EnterpriseAgent(SovereignAgent):
    """Profit-maximising firm: relocates supply chains to bypass tariffs."""

    role = AgentRole.ENTERPRISE
    priority = 30

    def evaluate(self, state, world, ctx):
        proposals: list[PolicyProposal] = []
        # If a major partner tariffs us, margins crash -> offshore production to
        # the lowest-friction alternative to bypass the barrier.
        tariffer, rate = world.worst_incoming_tariff(state.code)
        if tariffer and rate > 0.2:
            alt = world.cheapest_alternative(exclude={state.code, tariffer})
            if alt:
                fraction = _clamp(rate, 0.0, 0.6)
                world.trade_matrix.reroute(state.code, alt, tariffer, fraction)
                proposals.append(PolicyProposal(
                    role=self.role, code=state.code, field="export_index",
                    delta=-4.2 * rate, reason="margin_crash",
                    narrative=(f"{state.name} enterprises shift "
                               f"{fraction*100:.0f}% of production to "
                               f"{world.node(alt).state.name} to bypass tariffs"),
                ))
                # The capturing nation gains share.
                proposals.append(PolicyProposal(
                    role=self.role, code=alt, field="export_index",
                    delta=3.1 * rate, reason="share_capture",
                    narrative=(f"{world.node(alt).state.name} captures diverted "
                               f"supply-chain share (+{3.1*rate:.1f} export index)"),
                ))
        return proposals


class PublicAgent(SovereignAgent):
    """The masses: consumer confidence, wage-inflation tolerance, unrest."""

    role = AgentRole.PUBLIC
    priority = 40

    def evaluate(self, state, world, ctx):
        proposals: list[PolicyProposal] = []
        cost_of_living = max(0.0, state.inflation - state.wage_inflation_tolerance * 0.06)
        joblessness = max(0.0, state.unemployment - 0.05)
        unrest_target = _clamp(0.1 + 6.0 * cost_of_living + 4.0 * joblessness, 0.0, 1.0)
        delta = (unrest_target - state.social_unrest) * 0.2
        if abs(delta) > 1e-3:
            proposals.append(PolicyProposal(
                role=self.role, code=state.code, field="social_unrest",
                delta=delta, reason="social_dynamics",
                narrative=(f"{state.name} public stress "
                           f"{'rises' if delta > 0 else 'eases'} on "
                           f"{state.inflation*100:.1f}% inflation"),
            ))
            proposals.append(PolicyProposal(
                role=self.role, code=state.code, field="consumer_confidence",
                delta=-0.5 * max(0.0, delta), reason="confidence_erosion",
            ))
        return proposals


# ---------------------------------------------------------------------------
# Stateful scenario chronology (the branching memory)
# ---------------------------------------------------------------------------
@dataclass
class ScenarioNode:
    """One immutable state fork in the chronological event branch."""

    node_id: str
    parent_id: str | None
    label: str
    trigger: str
    sim_day: int
    created_at: datetime
    state_digest: dict[str, Any]
    facts: list[str] = field(default_factory=list)


class ScenarioChronology:
    """Append-only tree of forked world states (Scenario_A -> A.1 -> A.1.b)."""

    def __init__(self) -> None:
        self._nodes: dict[str, ScenarioNode] = {}
        self._active: str | None = None
        self._child_counter: dict[str, itertools.count] = {}

    @property
    def active_id(self) -> str | None:
        return self._active

    def fork(
        self, label: str, trigger: str, sim_day: int, state_digest: dict[str, Any],
        facts: list[str] | None = None,
    ) -> ScenarioNode:
        parent = self._active
        node = ScenarioNode(
            node_id=self._next_id(parent),
            parent_id=parent,
            label=label,
            trigger=trigger,
            sim_day=sim_day,
            created_at=datetime.now(timezone.utc),
            state_digest=state_digest,
            facts=facts or [],
        )
        self._nodes[node.node_id] = node
        self._active = node.node_id
        return node

    def _next_id(self, parent: str | None) -> str:
        if parent is None:
            return "A"
        counter = self._child_counter.setdefault(parent, itertools.count(1))
        suffix = next(counter)
        # A.1, A.1.b style: numeric then alpha alternation by depth parity.
        depth = parent.count(".")
        tag = str(suffix) if depth % 2 == 0 else chr(ord("a") + suffix - 1)
        return f"{parent}.{tag}"

    def lineage(self, node_id: str | None = None) -> list[ScenarioNode]:
        node_id = node_id or self._active
        chain: list[ScenarioNode] = []
        while node_id is not None:
            node = self._nodes[node_id]
            chain.append(node)
            node_id = node.parent_id
        return list(reversed(chain))

    def snapshot(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "nodes": {
                nid: {
                    "parent": n.parent_id, "label": n.label,
                    "trigger": n.trigger, "sim_day": n.sim_day,
                    "facts": n.facts,
                }
                for nid, n in self._nodes.items()
            },
        }


# ---------------------------------------------------------------------------
# The world graph + 5-phase integration
# ---------------------------------------------------------------------------
class SovereignWorldGraph:
    """The sovereign multi-agent world, wired into the 5-phase Tick Engine.

    Phase mapping:
        SNAPSHOT          -- freeze per-nation state into the tick context
        APPLY_EVENTS      -- ingest queued structural mutations (user tariffs)
        RESOLVE_CONFLICTS -- agents deliberate; proposals collected & adjudicated
        UPDATE_WORLD      -- apply proposals, propagate causal chain, fork history
        EMIT_SIGNALS      -- publish world.macro + narrative facts onto the bus
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self.nodes: dict[str, SovereignNode] = {}
        self.trade_matrix = GlobalTradeMatrix()
        self.chronology = ScenarioChronology()
        self._pending_mutations: list[PolicyProposal] = []
        self._tick_proposals: list[PolicyProposal] = []
        self._tick_facts: list[str] = []

    # -- construction ---------------------------------------------------------
    def add_nation(self, state: SovereignState) -> SovereignNode:
        node = SovereignNode(state)
        self.nodes[state.code] = node
        return node

    def node(self, code: str) -> SovereignNode:
        return self.nodes[code]

    def codes(self) -> list[str]:
        return list(self.nodes.keys())

    # -- graph helpers --------------------------------------------------------
    def worst_incoming_tariff(self, code: str) -> tuple[str | None, float]:
        worst_rate = 0.0
        worst_src: str | None = None
        for src in self.codes():
            if src == code:
                continue
            rate = self.trade_matrix.tariff(src, code)
            if rate > worst_rate:
                worst_rate, worst_src = rate, src
        return worst_src, worst_rate

    def cheapest_alternative(self, exclude: set[str]) -> str | None:
        candidates = [
            (self.node(c).state.supply_chain_friction, c)
            for c in self.codes() if c not in exclude
        ]
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]

    # -- external mutation API (from REST / LLM scenario) ---------------------
    def queue_tariff(self, source: str, target: str, rate: float) -> None:
        """Enqueue a user tariff mutation for the next tick's APPLY_EVENTS."""
        self._pending_mutations.append(PolicyProposal(
            role=AgentRole.GOVERNMENT, code=source, field="__tariff__",
            delta=rate, reason="user_mutation", tariff_edge=(source, target),
            narrative=f"{source} imposes a {rate*100:.0f}% import tariff on {target}",
        ))

    def queue_mutation(self, code: str, field: str, value: float) -> bool:
        """Enqueue an absolute-set mutation of a sovereign state field.

        Returns ``True`` when ``field`` maps to a real :class:`SovereignState`
        attribute (so the caller can report whether the sovereign graph will
        fork the chronology on the next tick), else ``False``.
        """
        node = self.nodes.get(code.upper())
        if node is None or not hasattr(node.state, field):
            return False
        self._pending_mutations.append(PolicyProposal(
            role=AgentRole.GOVERNMENT, code=code.upper(), field=f"__set__:{field}",
            delta=value, reason="user_mutation",
            narrative=f"{code.upper()} sets {field} to {value:.4g}",
        ))
        return True

    # -- 5-phase registration -------------------------------------------------
    def register(self, pipeline: TickPipeline) -> None:
        pipeline.register(TickPhase.SNAPSHOT, self._phase_snapshot, priority=50)
        pipeline.register(TickPhase.APPLY_EVENTS, self._phase_apply_events, priority=50)
        pipeline.register(TickPhase.RESOLVE_CONFLICTS, self._phase_resolve, priority=50)
        pipeline.register(TickPhase.UPDATE_WORLD, self._phase_update, priority=60)
        pipeline.register(TickPhase.EMIT_SIGNALS, self._phase_emit, priority=50)
        logger.info("SovereignWorldGraph wired into 5-phase tick pipeline")

    async def _phase_snapshot(self, ctx: TickContext) -> None:
        self._tick_proposals = []
        self._tick_facts = []
        ctx.frozen_state["world_sovereign"] = {
            c: n.state.snapshot() for c, n in self.nodes.items()
        }

    async def _phase_apply_events(self, ctx: TickContext) -> None:
        for mut in self._pending_mutations:
            if mut.field == "__tariff__" and mut.tariff_edge:
                src, tgt = mut.tariff_edge
                self.trade_matrix.set_tariff(src, tgt, mut.delta)
                self._tick_facts.append(mut.narrative)
            elif mut.field.startswith("__set__:"):
                fname = mut.field.split(":", 1)[1]
                node = self.nodes.get(mut.code)
                if node is not None and hasattr(node.state, fname):
                    setattr(node.state, fname, mut.delta)
                    self._tick_facts.append(mut.narrative)
        # Fork the chronology on any user-driven structural mutation.
        if self._pending_mutations:
            digest = {c: n.state.snapshot() for c, n in self.nodes.items()}
            trigger = "; ".join(m.narrative for m in self._pending_mutations)
            fork = self.chronology.fork(
                label=f"day-{ctx.sim_day}", trigger=trigger,
                sim_day=ctx.sim_day, state_digest=digest,
                facts=list(self._tick_facts),
            )
            logger.info("chronology forked -> %s (%s)", fork.node_id, trigger)
        self._pending_mutations = []

    async def _phase_resolve(self, ctx: TickContext) -> None:
        # Each nation's agents deliberate; the trade matrix is mutated in-place
        # by EnterpriseAgents (supply-chain reroute) during deliberation.
        for node in self.nodes.values():
            self._tick_proposals.extend(node.deliberate(self, ctx))

    async def _phase_update(self, ctx: TickContext) -> None:
        for prop in self._tick_proposals:
            target = self.nodes.get(prop.code)
            if target is None:
                continue
            if prop.tariff_edge:
                src, tgt = prop.tariff_edge
                self.trade_matrix.set_tariff(src, tgt, self.trade_matrix.tariff(src, tgt) + prop.delta)
            if prop.field not in ("__tariff__",):
                target.state.apply(prop.field, prop.delta)
            if prop.narrative:
                self._tick_facts.append(prop.narrative)
        # Secondary macro physics: growth responds to rates & export index.
        for node in self.nodes.values():
            s = node.state
            s.gdp_growth += 0.02 * (0.03 - s.policy_rate) + 0.0005 * (s.export_index - 100.0)
            s.gdp_growth = _clamp(s.gdp_growth, -0.15, 0.15)

    async def _phase_emit(self, ctx: TickContext) -> None:
        ctx.signals["world_nations"] = len(self.nodes)
        # Published on a DISTINCT topic (``world.sovereign``) so the advanced
        # multi-agent graph never collides with the legacy WorldKernel's
        # ``world.macro`` payload consumed by the existing dashboard read-model.
        await self._bus.publish(
            "world.sovereign",
            sim_day=ctx.sim_day,
            countries={c: n.state.snapshot() for c, n in self.nodes.items()},
            trade=self.trade_matrix.snapshot(),
            chronology=self.chronology.snapshot(),
        )
        for fact in self._tick_facts:
            await self._bus.publish("world.micro_impact", sim_day=ctx.sim_day, fact=fact)

    # -- read model -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "countries": {c: n.state.snapshot() for c, n in self.nodes.items()},
            "trade": self.trade_matrix.snapshot(),
            "chronology": self.chronology.snapshot(),
        }


def default_world(bus: EventBus) -> SovereignWorldGraph:
    """A seeded five-nation world: USA, China, Eurozone, Japan, Vietnam."""
    graph = SovereignWorldGraph(bus)
    graph.add_nation(SovereignState("USA", "United States", gdp=2.7e13, corporate_tax=0.21))
    graph.add_nation(SovereignState("CHN", "China", gdp=1.8e13, corporate_tax=0.25, supply_chain_friction=0.04))
    graph.add_nation(SovereignState("EUR", "Eurozone", gdp=1.5e13, corporate_tax=0.23))
    graph.add_nation(SovereignState("JPN", "Japan", gdp=4.2e12, corporate_tax=0.30))
    graph.add_nation(SovereignState("VNM", "Vietnam", gdp=4.5e11, corporate_tax=0.20, supply_chain_friction=0.02))
    # Seed baseline trade intensities.
    for a, b, w in [
        ("CHN", "USA", 0.8), ("VNM", "USA", 0.3), ("EUR", "USA", 0.5),
        ("JPN", "USA", 0.4), ("USA", "CHN", 0.6), ("CHN", "EUR", 0.5),
    ]:
        graph.trade_matrix.set_flow(a, b, w)
    graph.chronology.fork(
        label="genesis", trigger="world initialised", sim_day=0,
        state_digest={c: n.state.snapshot() for c, n in graph.nodes.items()},
    )
    _ = uuid  # retained for external correlation-id use
    return graph
