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

        crisis = (internal.label == "VOLATILE" and internal.confidence > 0.4) or m.is_crisis()
        if not crisis:
            return AgentDecision()

        # Flight intensity: severity of the perceived crisis, damped by appetite.
        sev = _clamp(0.5 * m.stress + 0.5 * (internal.confidence if internal.label == "VOLATILE" else 0.0), 0.0, 1.0)
        flee = _clamp(sev * (1.15 - self._appetite), 0.0, 1.0)
        if flee < 0.05:
            return AgentDecision()

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

        if internal.label == "VOLATILE" and internal.confidence > 0.4:
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
            metrics={"capital_flight_usd": flight_usd, "yield_shock_bps": 0.0035 * flee * 1e4},
            tags=("capital_flight", f"regime:{internal.label}"),
        )
        return AgentDecision(adjustments=adjustments, facts=[fact], micro_shocks=[micro])


# ===========================================================================
#  Government AI :: the sovereigns
# ===========================================================================
class GovernmentAI(MarketAwareAgent):
    """Sovereign policy that retaliates against capital drains and market attacks."""

    name = "government_ai"
    priority = 20  # runs AFTER corporates so it can read their capital-flight writes

    def __init__(self, defend_speed: float = 1.0) -> None:
        self._defend = defend_speed

    def evaluate(self, code: str, world: WorldState, board: TickBlackboard) -> AgentDecision:
        c = world.countries[code]
        m = board.market

        drain = board.capital_flight.get(code, 0.0)
        drain_ratio = drain / max(1.0, 0.10 * c.gdp)          # vs 10% of GDP
        # A persistent AI short during high vol reads as a coordinated attack.
        attack = max(0.0, -m.ai_direction) * m.ai_confidence * m.volatility

        pressure = _clamp(0.7 * drain_ratio + 0.6 * attack, 0.0, 1.0)
        if pressure < 0.06:
            return AgentDecision()

        defend = _clamp(pressure * self._defend, 0.0, 1.0)
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
            metrics={"defense_intensity": defend, "rate_hike_bps": 0.0045 * defend * 1e4},
            tags=("capital_controls", "retaliation"),
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

    def __init__(self, unrest_threshold: float = 0.6) -> None:
        self._threshold = unrest_threshold

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
        if projected > self._threshold and delta > 0.0:
            facts.append(CausalFact(
                actor="Societal AI",
                country=code,
                action="mass mobilisation and strikes erupted",
                cause=(f"civil-unrest index breached {self._threshold:.2f} under market "
                       f"stress {m.stress:.2f} and {c.monetary.inflation_cpi*100:.1f}% inflation"),
                effect=(f"{c.name} political stability sliding; investors price a "
                        f"sovereign risk premium"),
                level="danger",
                metrics={"unrest_index": projected, "stress": m.stress},
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
        return AgentDecision(adjustments=adjustments, facts=facts, micro_shocks=micro_shocks)


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
