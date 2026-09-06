"""ECONITH :: ai.simulator_engine.agents

Intelligence-driven, market-aware multi-agent ensemble for ECONITH World.

Unlike the classic macro reaction models (``reaction_models.py``, which see only
the :class:`WorldState`), these agents are *explicitly aware of the live Quant
market* via a :class:`~ai.simulator_engine.market_context.MarketSnapshot`. They
are the load-bearing half of the Quant<->World feedback loop:

* :class:`CorporateAI` -- quant-driven conglomerates. Each runs its **own**
  internal regime classifier (HMM/GMM-style) over a firm-specific view of the
  tape. When it perceives a Crisis / High-Volatility regime it triggers capital
  flight, dumps sovereign assets (yield blowout) and relocates supply chains --
  and emits a microstructural sell-shock back into the market.

* :class:`GovernmentAI` -- the sovereigns. Reacts to both macro metrics and
  *market attacks*: when conglomerates drain capital or the AI ensemble presses
  a persistent short, it retaliates with capital controls, defensive rate hikes
  and trade barriers against its least-trusted rival.

* :class:`SocietalSentimentAI` -- the masses. Integrates market volatility with
  decaying macro vectors into a dynamic civil-unrest index; past a threshold it
  fires systemic instability events that feed *back* into the market.

Agents communicate within a tick through a shared :class:`TickBlackboard`
(a cooperative/adversarial game board), and best-respond to it -- the kernel
applies all proposals simultaneously.
"""
from __future__ import annotations

import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ai.regime.classifier import HeuristicRegimeClassifier
from ai.simulator_engine.adaptive_policy import ActionOption, AdaptivePolicy, PolicyDecision
from ai.simulator_engine.cross_impact import MicrostructuralVolatilityVector
from ai.simulator_engine.macro_vectors import WorldState
from ai.simulator_engine.market_context import MarketSnapshot
from ai.simulator_engine.narrative import CausalFact
from ai.simulator_engine.reaction_models import Adjustment

__all__ = [
    "TickBlackboard",
    "AgentDecision",
    "MarketAwareAgent",
    "CorporateAI",
    "GovernmentAI",
    "SocietalSentimentAI",
    "default_intelligent_agents",
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ===========================================================================
#  Shared per-tick game board + decision envelope
# ===========================================================================
@dataclass(slots=True)
class TickBlackboard:
    """Mutable intra-tick blackboard the agents read/write cooperatively.

    Enables the multi-agent *game*: Corporate AIs post capital-flight pressure,
    which the Government AIs read in the same tick to justify retaliation.
    """

    market: MarketSnapshot
    capital_flight: dict[str, float] = field(default_factory=dict)     # code -> USD
    corporate_distress: dict[str, float] = field(default_factory=dict)  # code -> 0..1
    govt_retaliation: dict[str, float] = field(default_factory=dict)    # code -> 0..1

    def add_flight(self, code: str, usd: float, distress: float) -> None:
        self.capital_flight[code] = self.capital_flight.get(code, 0.0) + usd
        self.corporate_distress[code] = max(self.corporate_distress.get(code, 0.0), distress)


@dataclass(slots=True)
class AgentDecision:
    """Everything an agent proposes for one country in one tick."""

    adjustments: list[Adjustment] = field(default_factory=list)
    facts: list[CausalFact] = field(default_factory=list)
    micro_shocks: list[MicrostructuralVolatilityVector] = field(default_factory=list)

    def extend(self, other: "AgentDecision") -> None:
        self.adjustments.extend(other.adjustments)
        self.facts.extend(other.facts)
        self.micro_shocks.extend(other.micro_shocks)


class MarketAwareAgent(ABC):
    """Abstract agent aware of both the world and the live market tape."""

    name: str = "base"
    #: agents run in ascending priority so later agents see earlier blackboard writes
    priority: int = 0

    @abstractmethod
    def evaluate(
        self, code: str, world: WorldState, board: TickBlackboard
    ) -> AgentDecision:
        raise NotImplementedError


# ===========================================================================
#  Corporate AI :: the quant-driven conglomerates
# ===========================================================================
class CorporateAI(MarketAwareAgent):
    """Conglomerates with an internal regime classifier and capital mobility."""

    name = "corporate_ai"
    priority = 10

    def __init__(
        self,
        risk_appetite: float = 0.5,   # 0 (skittish) .. 1 (risk-loving)
        window: int = 45,
        seed: int | None = None,
    ) -> None:
        self._appetite = _clamp(risk_appetite, 0.0, 1.0)
        # Each conglomerate runs its OWN classifier -> heterogeneous perception.
        self._clf = HeuristicRegimeClassifier(window=window)
        self._rng = random.Random(seed)
        self._policy = AdaptivePolicy(
            (
                ActionOption("hold", {"stress": -0.15}, bias=0.15),
                ActionOption(
                    "expand",
                    {"calm": 0.9, "confidence": 0.8, "bullish": 0.35, "stress": -1.2},
                    bias=-0.15,
                ),
                ActionOption(
                    "delever",
                    {"stress": 1.7, "sell_pressure": 0.9, "volatile": 0.8,
                     "confidence": -0.6},
                    bias=-0.55,
                ),
            ),
            learning_rate=0.14,
            exploration=0.06,
            temperature=0.28,
            seed=(seed or 0) + 1_000,
        )

    def _perceive(self, code: str, world: WorldState, m: MarketSnapshot):
        """Feed a firm-specific view of the tape into the internal classifier."""
        c = world.countries[code]
        confidence = c.geopolitical.business_confidence
        # Firm-perceived order flow: bearish when the market sells off or home
        # confidence sags; risk-loving firms discount the noise.
        flow = -m.sell_pressure + (confidence - 0.6) + m.ai_direction * 0.3
        # Dispersion the firm "sees": scaled by market vol and its own timidity.
        disp = m.volatility * (1.3 - self._appetite)
        obi_feed = _clamp(flow, -1.0, 1.0)
        vd_feed = disp * 320.0 * self._rng.choice((-1.0, 1.0))
        return self._clf.classify({"obi": obi_feed, "volume_delta": vd_feed})

    def evaluate(self, code: str, world: WorldState, board: TickBlackboard) -> AgentDecision:
        c = world.countries[code]
        m = board.market
        internal = self._perceive(code, world, m)
        confidence = c.geopolitical.business_confidence
        decision = self._policy.decide(
            code,
            features={
                "stress": m.stress,
                "calm": 1.0 - m.stress,
                "sell_pressure": m.sell_pressure,
                "bullish": max(0.0, m.ai_direction) * m.ai_confidence,
                "confidence": confidence,
                "volatile": (
                    internal.confidence if internal.label == "VOLATILE" else 0.0
                ),
            },
            utility=self._utility(code, world, m),
        )
        if decision.action == "hold":
            return AgentDecision()
        if decision.action == "expand":
            return self._expand(code, world, m, internal, decision)

        # Flight intensity: severity of the perceived crisis, damped by appetite.
        sev = _clamp(
            0.35
            + 0.4 * m.stress
            + 0.25 * (
                internal.confidence if internal.label == "VOLATILE" else m.sell_pressure
            ),
            0.0,
            1.0,
        )
        flee = _clamp(sev * (1.15 - self._appetite), 0.0, 1.0)

        mobile = c.fiscal.fdi_inflow + 0.25 * c.fiscal.foreign_reserves
        flight_usd = mobile * 0.16 * flee

        adjustments = [
            # Capital flight: inflows reverse, reserves are drawn down.
            Adjustment(code, "fiscal", "fdi_inflow", -c.fiscal.fdi_inflow * 0.12 * flee,
                       reason="capital_flight"),
            Adjustment(code, "fiscal", "foreign_reserves", -0.5 * flight_usd,
                       reason="reserve_drawdown"),
            # Currency depreciates as capital leaves.
            Adjustment(code, "monetary", "fx_spot", c.monetary.fx_spot * 0.018 * flee,
                       reason="fx_depreciation"),
            # Sovereign assets dumped -> long-end yields blow out.
            Adjustment(code, "monetary", "yield_10y", 0.0035 * flee,
                       reason="sovereign_dump"),
            # Supply chains relocate away from the stressed economy.
            Adjustment(code, "industrial", "supply_chain_friction", 0.03 * flee,
                       reason="supply_chain_relocation"),
            Adjustment(code, "fiscal", "export_index", -1.2 * flee,
                       reason="offshoring"),
            Adjustment(code, "industrial", "capacity_utilization", -0.01 * flee,
                       reason="deleveraging"),
        ]

        board.add_flight(code, flight_usd, flee)

        # The dump is itself a microstructural sell-shock fed back to the Quant
        # engine -- conglomerate deleveraging *is* order flow.
        micro = MicrostructuralVolatilityVector(
            volatility_multiplier=1.0 + 0.9 * flee,
            order_flow_shock=-0.45 * flee,
            liquidity_drain=0.25 * flee,
            spread_widening_bps=12.0 * flee,
            regime_pressure={"VOLATILE": 1.3 * flee, "CALM": -1.0 * flee,
                             "TRENDING": 0.2 * flee, "MEAN_REVERTING": 0.0},
            duration_ticks=3,
            origin=f"corporate_flight:{code}",
            headline=f"Conglomerate deleveraging out of {c.name}",
        )

        if internal.label == "VOLATILE" and internal.confidence > 0.55:
            trigger = (f"an internally-classified VOLATILE regime "
                       f"({internal.confidence*100:.0f}% conf)")
        else:
            trigger = f"a systemic market-crisis print (stress {m.stress:.2f})"
        fact = CausalFact(
            actor="Corporate AI",
            country=code,
            action=(f"repatriated ${flight_usd / 1e9:.1f}B, dumped sovereign paper and "
                    f"relocated supply chains"),
            cause=f"{trigger} with sell-pressure {m.sell_pressure:.2f}",
            effect=(f"{c.name} 10y yields +{0.0035*flee*1e4:.0f}bps, currency "
                    f"-{0.018*flee*100:.1f}%, supply-chain friction rising"),
            level="danger",
            metrics={
                "capital_flight_usd": flight_usd,
                "yield_shock_bps": 0.0035 * flee * 1e4,
                "policy_confidence": decision.confidence,
            },
            tags=("capital_flight", f"regime:{internal.label}"),
        )
        return AgentDecision(adjustments=adjustments, facts=[fact], micro_shocks=[micro])

    @staticmethod
    def _utility(code: str, world: WorldState, m: MarketSnapshot) -> float:
        c = world.countries[code]
        return (
            0.35 * c.geopolitical.business_confidence
            + 0.25 * c.industrial.capacity_utilization
            + 0.20 * _clamp(c.fiscal.export_index / 120.0, 0.0, 1.0)
            + 0.20 * _clamp((c.gdp_growth + 0.10) / 0.20, 0.0, 1.0)
            - 0.45 * m.stress
        )

    def _expand(
        self,
        code: str,
        world: WorldState,
        m: MarketSnapshot,
        internal,
        decision: PolicyDecision,
    ) -> AgentDecision:
        """Execute an expansion selected by the adaptive objective policy."""
        c = world.countries[code]
        confidence = c.geopolitical.business_confidence
        drive = _clamp((confidence - 0.5) * 2.0 + 0.3 * max(0.0, m.ai_direction), 0.0, 1.0)
        drive *= 0.5 + 0.5 * self._appetite
        drive = max(0.08, drive) * (0.55 + 0.45 * decision.confidence)

        invest_usd = (0.08 * c.fiscal.fdi_inflow + 0.015 * c.fiscal.foreign_reserves) * drive
        adjustments = [
            Adjustment(code, "fiscal", "fdi_inflow", c.fiscal.fdi_inflow * 0.05 * drive,
                       reason="expansion_capex"),
            Adjustment(code, "industrial", "capacity_utilization", 0.006 * drive,
                       reason="capacity_buildout"),
            Adjustment(code, "labor", "unemployment", -0.0012 * drive,
                       reason="hiring_wave"),
            Adjustment(code, "fiscal", "export_index", 0.5 * drive,
                       reason="new_orders"),
            Adjustment(code, "industrial", "supply_chain_friction", -0.015 * drive,
                       reason="supply_chain_reshoring"),
        ]
        fact = CausalFact(
            actor="Corporate AI",
            country=code,
            action=f"committed ${invest_usd / 1e9:.1f}B to new capacity and hiring",
            cause=(f"a {internal.label} regime with business confidence "
                   f"{confidence:.2f} and market stress {m.stress:.2f}"),
            effect=f"{c.name} capacity utilisation, exports and hiring tick up",
            level="ok",
            metrics={
                "investment_usd": invest_usd,
                "confidence": confidence,
                "policy_confidence": decision.confidence,
            },
            tags=("corporate_expansion", f"regime:{internal.label}"),
        )
        return AgentDecision(adjustments=adjustments, facts=[fact])


# ===========================================================================
#  Government AI :: the sovereigns
# ===========================================================================
class GovernmentAI(MarketAwareAgent):
    """Sovereign policy that retaliates against capital drains and market attacks."""

    name = "government_ai"
    priority = 20  # runs AFTER corporates so it can read their capital-flight writes

    def __init__(self, defend_speed: float = 1.0, seed: int | None = 31) -> None:
        self._defend = defend_speed
        self._rng = random.Random(seed)
        self._policy = AdaptivePolicy(
            (
                ActionOption("hold", bias=0.18),
                ActionOption(
                    "stimulate",
                    {"slack": 1.0, "inflation_room": 0.8, "stress": -0.7},
                    bias=-0.30,
                ),
                ActionOption(
                    "defend",
                    {"capital_pressure": 1.5, "attack": 0.9, "stress": 0.45},
                    bias=-0.45,
                ),
            ),
            learning_rate=0.12,
            exploration=0.05,
            temperature=0.30,
            seed=(seed or 0) + 2_000,
        )

    def evaluate(self, code: str, world: WorldState, board: TickBlackboard) -> AgentDecision:
        c = world.countries[code]
        m = board.market

        drain = board.capital_flight.get(code, 0.0)
        drain_ratio = drain / max(1.0, 0.10 * c.gdp)          # vs 10% of GDP
        # A persistent AI short during high vol reads as a coordinated attack.
        attack = max(0.0, -m.ai_direction) * m.ai_confidence * m.volatility

        pressure = _clamp(0.7 * drain_ratio + 0.6 * attack, 0.0, 1.0)
        slack = _clamp(max(0.0, c.labor.unemployment - 0.045) * 14.0, 0.0, 1.0)
        inflation_room = _clamp(
            max(0.0, 0.040 - c.monetary.inflation_cpi) * 30.0, 0.0, 1.0
        )
        policy = self._policy.decide(
            code,
            features={
                "capital_pressure": pressure,
                "attack": attack,
                "stress": m.stress,
                "slack": slack,
                "inflation_room": inflation_room,
            },
            utility=self._utility(code, world, m),
        )
        if policy.action == "hold":
            return AgentDecision()
        if policy.action == "stimulate":
            return self._stimulate(code, world, m, policy)

        defend = _clamp(max(0.08, pressure) * self._defend, 0.0, 1.0)
        board.govt_retaliation[code] = defend
        adjustments: list[Adjustment] = [
            # Defensive rate hike to arrest the currency slide.
            Adjustment(code, "monetary", "interest_rate", 0.0045 * defend,
                       reason="currency_defense"),
            # Capital controls: raise reserve requirements, restrict outflows.
            Adjustment(code, "monetary", "reserve_requirement", 0.012 * defend,
                       reason="capital_controls"),
            Adjustment(code, "fiscal", "fdi_outflow", -c.fiscal.fdi_outflow * 0.05 * defend,
                       reason="outflow_restriction"),
        ]

        # Trade barrier against the least-trusted meaningful rival.
        rival = self._least_trusted(code, world)
        events_extra = ""
        if rival and (drain_ratio > 0.15 or attack > 0.25):
            adjustments.append(
                Adjustment(code, "tariff", rival, 0.05 * defend, reason="trade_barrier")
            )
            events_extra = f" and slapped tariffs on {world.countries[rival].name}"

        fact = CausalFact(
            actor="Government AI",
            country=code,
            action=(f"imposed capital controls, hiked policy rates "
                    f"+{0.0045*defend*1e4:.0f}bps{events_extra}"),
            cause=(f"${drain/1e9:.1f}B in capital flight and a "
                   f"{attack:.2f} market-attack signal"),
            effect=(f"reserve requirement +{0.012*defend*100:.2f}pp to stem the "
                    f"outflow and defend {c.name}'s sovereign balance sheet"),
            level="danger" if defend > 0.5 else "warn",
            metrics={
                "defense_intensity": defend,
                "rate_hike_bps": 0.0045 * defend * 1e4,
                "policy_confidence": policy.confidence,
            },
            tags=("capital_controls", "retaliation"),
        )
        return AgentDecision(adjustments=adjustments, facts=[fact])

    @staticmethod
    def _utility(code: str, world: WorldState, m: MarketSnapshot) -> float:
        c = world.countries[code]
        inflation_gap = abs(c.monetary.inflation_cpi - 0.025)
        return (
            0.30 * _clamp((c.gdp_growth + 0.10) / 0.20, 0.0, 1.0)
            + 0.25 * c.geopolitical.political_stability
            + 0.20 * c.geopolitical.public_approval
            - 0.30 * _clamp(inflation_gap / 0.10, 0.0, 1.0)
            - 0.25 * c.labor.unemployment
            - 0.20 * m.stress
        )

    def _stimulate(
        self,
        code: str,
        world: WorldState,
        m: MarketSnapshot,
        policy: PolicyDecision,
    ) -> AgentDecision:
        """Execute stimulus selected by the adaptive objective policy."""
        c = world.countries[code]
        slack = max(0.0, c.labor.unemployment - 0.045) * 14.0     # labour slack
        cool = max(0.0, 0.040 - c.monetary.inflation_cpi) * 30.0  # inflation room
        room = _clamp(0.6 * slack + 0.4 * cool, 0.0, 1.0)

        ease = _clamp(max(0.06, room) * self._defend, 0.0, 1.0)
        adjustments = [
            Adjustment(code, "monetary", "interest_rate", -0.0020 * ease,
                       reason="growth_support"),
            Adjustment(code, "fiscal", "public_investment_pct", 0.002 * ease,
                       reason="infrastructure_program"),
            Adjustment(code, "industrial", "infrastructure_index", 0.004 * ease,
                       reason="public_works"),
            Adjustment(code, "geopolitical", "consumer_confidence", 0.01 * ease,
                       reason="stimulus_optimism"),
        ]
        fact = CausalFact(
            actor="Government AI",
            country=code,
            action=(f"cut rates {0.0020 * ease * 1e4:.0f}bps and launched a "
                    f"public-investment program"),
            cause=(f"{c.labor.unemployment * 100:.1f}% unemployment with "
                   f"{c.monetary.inflation_cpi * 100:.1f}% inflation left policy room"),
            effect=f"{c.name} infrastructure spending and consumer confidence rise",
            level="ok",
            metrics={
                "rate_cut_bps": 0.0020 * ease * 1e4,
                "stimulus_intensity": ease,
                "policy_confidence": policy.confidence,
            },
            tags=("fiscal_stimulus",),
        )
        return AgentDecision(adjustments=adjustments, facts=[fact])

    @staticmethod
    def _least_trusted(code: str, world: WorldState) -> str | None:
        rivals = [(world.alliance(code, o), o) for o in world.codes() if o != code]
        if not rivals:
            return None
        rivals.sort(key=lambda t: t[0])   # lowest trust first
        trust, rival = rivals[0]
        return rival if trust < 0.5 else None


# ===========================================================================
#  Societal Sentiment AI :: the masses
# ===========================================================================
class SocietalSentimentAI(MarketAwareAgent):
    """Dynamic civil-unrest index coupling market volatility to social stability."""

    name = "societal_ai"
    priority = 30  # last: integrates the fallout of corporate + government moves

    def __init__(self, unrest_threshold: float = 0.6, seed: int | None = 47) -> None:
        self._threshold = unrest_threshold
        self._rng = random.Random(seed)
        self._policy = AdaptivePolicy(
            (
                ActionOption("steady", bias=0.15),
                ActionOption(
                    "mobilise",
                    {"unrest": 1.3, "worsening": 0.8, "stress": 0.5},
                    bias=-0.65,
                ),
                ActionOption(
                    "cut_spending",
                    {"cost_of_living": 1.1, "joblessness": 0.5, "stress": 0.25},
                    bias=-0.45,
                ),
                ActionOption(
                    "recover",
                    {"calm": 0.7, "improving": 0.9, "confidence": 0.45},
                    bias=-0.35,
                ),
            ),
            learning_rate=0.13,
            exploration=0.06,
            temperature=0.30,
            seed=(seed or 0) + 3_000,
        )

    def evaluate(self, code: str, world: WorldState, board: TickBlackboard) -> AgentDecision:
        c = world.countries[code]
        g = c.geopolitical
        m = board.market

        # Grievance drivers: market turmoil, cost-of-living, joblessness, and
        # the visible pain of any government austerity/controls this tick.
        cost_of_living = max(0.0, c.monetary.inflation_cpi - 0.03) * 9.0
        joblessness = max(0.0, c.labor.unemployment - 0.05) * 8.0
        retaliation_pain = board.govt_retaliation.get(code, 0.0) * 0.4
        confidence_gap = max(0.0, 0.6 - g.consumer_confidence)

        target = _clamp(
            0.12
            + 0.38 * m.stress
            + 0.30 * cost_of_living
            + 0.28 * joblessness
            + 0.20 * confidence_gap
            + retaliation_pain,
            0.0, 1.0,
        )
        delta = (target - g.social_unrest_index) * 0.16
        adjustments = [
            Adjustment(code, "geopolitical", "social_unrest_index", delta,
                       reason="unrest_dynamics"),
            Adjustment(code, "geopolitical", "political_stability",
                       -0.05 * max(0.0, delta), reason="instability"),
            Adjustment(code, "geopolitical", "public_approval",
                       -0.06 * max(0.0, delta), reason="approval_erosion"),
        ]

        facts: list[CausalFact] = []
        micro_shocks: list[MicrostructuralVolatilityVector] = []
        projected = g.social_unrest_index + delta
        policy = self._policy.decide(
            code,
            features={
                "unrest": projected,
                "worsening": max(0.0, delta) * 10.0,
                "improving": max(0.0, -delta) * 10.0,
                "cost_of_living": _clamp(cost_of_living, 0.0, 1.0),
                "joblessness": _clamp(joblessness, 0.0, 1.0),
                "stress": m.stress,
                "calm": 1.0 - m.stress,
                "confidence": g.consumer_confidence,
            },
            utility=self._utility(code, world, m),
        )
        if policy.action == "mobilise":
            facts.append(CausalFact(
                actor="Societal AI",
                country=code,
                action="mass mobilisation and strikes erupted",
                cause=(f"civil-unrest index breached {self._threshold:.2f} under market "
                       f"stress {m.stress:.2f} and {c.monetary.inflation_cpi*100:.1f}% inflation"),
                effect=(f"{c.name} political stability sliding; investors price a "
                        f"sovereign risk premium"),
                level="danger",
                metrics={
                    "unrest_index": projected,
                    "stress": m.stress,
                    "policy_confidence": policy.confidence,
                },
                tags=("civil_unrest", "systemic_event"),
            ))
            # Unrest -> risk premium -> its own microstructural sell-shock.
            micro_shocks.append(MicrostructuralVolatilityVector(
                volatility_multiplier=1.0 + 0.5 * (projected - self._threshold),
                order_flow_shock=-0.25 * (projected - self._threshold),
                liquidity_drain=0.15,
                regime_pressure={"VOLATILE": 0.9, "CALM": -0.7,
                                 "TRENDING": 0.0, "MEAN_REVERTING": 0.0},
                duration_ticks=4,
                origin=f"civil_unrest:{code}",
                headline=f"Civil unrest risk premium on {c.name}",
            ))
        elif policy.action == "cut_spending":
            facts.append(CausalFact(
                actor="Societal AI",
                country=code,
                action="households cut discretionary spending",
                cause=(f"cost of living is biting with "
                       f"{c.monetary.inflation_cpi * 100:.1f}% inflation"),
                effect=f"{c.name} retail demand softens; wage demands build",
                level="warn",
                metrics={"inflation_pct": c.monetary.inflation_cpi * 100,
                         "unrest_index": projected,
                         "policy_confidence": policy.confidence},
                tags=("cost_of_living",),
            ))
        elif policy.action == "recover":
            facts.append(CausalFact(
                actor="Societal AI",
                country=code,
                action="consumer sentiment recovered",
                cause=(f"stable prices and a calm market "
                       f"(stress {m.stress:.2f}) rebuilt household confidence"),
                effect=f"{c.name} retail spending and approval ratings firm up",
                level="ok",
                metrics={"confidence": g.consumer_confidence,
                         "unrest_index": projected,
                         "policy_confidence": policy.confidence},
                tags=("consumer_recovery",),
            ))
        return AgentDecision(adjustments=adjustments, facts=facts, micro_shocks=micro_shocks)

    @staticmethod
    def _utility(code: str, world: WorldState, m: MarketSnapshot) -> float:
        c = world.countries[code]
        g = c.geopolitical
        purchasing_power = 1.0 - _clamp(c.monetary.inflation_cpi / 0.15, 0.0, 1.0)
        employment = 1.0 - _clamp(c.labor.unemployment, 0.0, 1.0)
        return (
            0.30 * purchasing_power
            + 0.25 * employment
            + 0.25 * g.consumer_confidence
            + 0.20 * g.political_stability
            - 0.35 * g.social_unrest_index
            - 0.15 * m.stress
        )


# ===========================================================================
#  Default ensemble (the DI seam)
# ===========================================================================
def default_intelligent_agents() -> list[MarketAwareAgent]:
    """Heterogeneous conglomerates + sovereigns + society.

    Multiple Corporate AIs with different risk appetites produce a realistic
    dispersion of firm behaviour (some flee early, some ride it out).
    """
    return [
        CorporateAI(risk_appetite=0.30, seed=101),   # skittish early-mover
        CorporateAI(risk_appetite=0.65, seed=202),   # patient conglomerate
        GovernmentAI(defend_speed=1.0),
        SocietalSentimentAI(unrest_threshold=0.6),
    ]
