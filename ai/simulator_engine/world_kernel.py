"""ECONITH :: ai.simulator_engine.world_kernel

The **Unified Simulation Kernel** -- the master macro/micro state machine that
closes the ECONITH feedback loop between geopolitics (World) and market
microstructure (Quant).

Driven by the Core Engine's ``time.tick`` (speed 1x-20x governed entirely by the
Time Engine), every tick the kernel:

    0. refreshes the live :class:`MarketContext` from the Quant EventBus topics
       it subscribes to (``ai.signal`` / ``indicator.*`` / ``alt.*`` /
       ``sentinel.status``) -- the Quant->World ingestion path,
    1. computes the standing **macro->micro** coupling
       (:func:`cross_impact.macro_to_micro`) and publishes it as
       ``world.micro_impact`` -- the shock signature the Quant engine ingests,
    2. gathers proposals from (a) the classic macro reaction models and (b) the
       intelligence-driven hierarchical cognitive world (Tier-1 governors,
       Tier-2 Nash bargaining, Tier-3 belief clusters) whose continuous
       actions and threshold-crossing events emerge from conflicting
       objective functions — never from hardcoded scenario scripts,
    3. computes the **quant->macro** feedback (:func:`cross_impact.quant_to_macro`)
       -- capital flight, FX depreciation, sovereign-yield blowout, imported
       inflation and civil-unrest pressure driven by the live tape,
    4. applies every proposal simultaneously (clamped) -> relaxes toward a
       Nash-style equilibrium, then steps derived macro (GDP / unemployment),
    5. synthesises a hyper-detailed causal narrative and publishes the rich
       world snapshot + a macro-driven ``world.quant_signal``.

Public API (``register`` / ``state_dict`` / ``country_dict`` / ``mutate_country``
/ ``set_tariff`` / ``apply_mutations``) is preserved for the FastAPI layer and
the LLM scenario engine.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from ai.simulator_engine.cross_impact import (
    CrisisEvent,
    CrisisType,
    DomainRandomizer,
    GeopoliticalCausalGraph,
    MacroFeedback,
    MicrostructuralVolatilityVector,
    macro_to_micro,
    quant_to_macro,
)
from ai.simulator_engine.macro_vectors import CountryState
from ai.simulator_engine.macro_vectors import WorldState, default_world
from ai.simulator_engine.market_context import MarketContext
from ai.simulator_engine.narrative import CausalFact, NarrativeEngine
from ai.simulator_engine.reaction_models import (
    Adjustment,
    ReactionModel,
    default_models,
)
from core.event_bus import Event, EventBus
from core.mode import get_mode_manager
from econith.world import MesaSovereignKernel
from econith.world.core.dialogue_orchestrator import DialogueOrchestrator
from econith.world.core.hierarchy_broker import HierarchyBroker

logger = logging.getLogger("econith.world.kernel")

SIM_START = date(2026, 1, 1)

# Logical (flat) field names -> (group | None, field). Used by the LLM scenario
# engine and any caller that thinks in plain economic terms.
LOGICAL_FIELDS: dict[str, tuple[str | None, str]] = {
    "interest_rate": ("monetary", "interest_rate"),
    "inflation": ("monetary", "inflation_cpi"),
    "tax": ("fiscal", "corporate_tax"),
    "corporate_tax": ("fiscal", "corporate_tax"),
    "unemployment": ("labor", "unemployment"),
    "gdp_growth": (None, "gdp_growth"),
    "defense": ("geopolitical", "defense_spending_pct"),
}

# Clamp bounds for the most-mutated fields (fraction-like unless noted).
_BOUNDS: dict[str, tuple[float, float]] = {
    "interest_rate": (0.0, 0.25),
    "inflation_cpi": (-0.03, 0.15),
    "inflation_ppi": (-0.03, 0.18),
    "corporate_tax": (0.0, 0.60),
    "individual_tax": (0.0, 0.75),
    "vat": (0.0, 0.40),
    "unemployment": (0.005, 0.45),
    "gdp_growth": (-0.20, 0.20),
    "export_index": (10.0, 400.0),
    "import_index": (10.0, 400.0),
    "trade_balance_pct": (-0.30, 0.30),
    "consumer_confidence": (0.05, 0.98),
    "business_confidence": (0.05, 0.98),
    "political_stability": (0.05, 0.99),
    "public_approval": (0.02, 0.99),
    "social_unrest_index": (0.0, 1.0),
    "defense_spending_pct": (0.0, 0.20),
    "fx_spot": (0.01, 1.0e6),
    # feedback-loop targets
    "reserve_requirement": (0.0, 0.50),
    "yield_10y": (-0.02, 0.15),
    "yield_2y": (-0.02, 0.15),
    "credit_growth": (-0.30, 0.40),
    "velocity_of_money": (0.5, 12.0),
    "foreign_reserves": (0.0, 1.0e14),
    "fdi_inflow": (0.0, 1.0e13),
    "fdi_outflow": (0.0, 1.0e13),
    "capacity_utilization": (0.20, 1.0),
    "supply_chain_friction": (0.0, 1.0),
    "public_investment_pct": (0.0, 0.20),
    "sanctions_exposure": (0.0, 1.0),
    "money_supply_m2": (1e9, 5e13),
}

_COMPANIES = ("Xamsung", "Pineapple", "Macrosoft", "Googol", "Volksauto")
_AMBIENT = (
    "{company} shifts production footprint amid shifting trade flows.",
    "{country} posts {dir} industrial output for the quarter.",
    "Ratings agency reviews {country} sovereign outlook.",
    "{company} signs strategic supply deal in {country}.",
)

_AGENT_ACTORS = frozenset({
    "Corporate AI", "Government AI", "Societal AI", "Sovereign",
    "Household", "Central Bank", "Labor",
})

# Which log source each narrative actor maps to (for the dashboard event feed).
_ACTOR_SOURCE = {
    "Corporate AI": "corporate",
    "Government AI": "government",
    "Societal AI": "society",
    "Household": "society",
    "Labor": "society",
    "Central Bank": "government",
    "Market": "regime",
    "Sovereign": "sovereign",
}

# Emergent event kind -> (actor, level). Narratives are rendered from metrics.
_EMERGENT_META: dict[str, tuple[str, str]] = {
    "labor_strike": ("Labor", "danger"),
    "safe_haven_migration": ("Household", "warn"),
    "demand_contraction": ("Household", "warn"),
    "demand_expansion": ("Household", "ok"),
}


def _clamp_field(field: str, value: float) -> float:
    lo, hi = _BOUNDS.get(field, (-1.0e15, 1.0e15))
    return max(lo, min(hi, value))


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ===========================================================================
#  Object-Oriented Entity Kernel :: autonomous CountryEntity
# ===========================================================================
@dataclass(slots=True)
class EntityDemographics:
    """Demographic & societal capacity vector (derived + evolving)."""

    birth_rate: float                 # births / population / yr
    aging_index: float                # elderly ratio proxy, 0 (young) .. 1 (aged)
    youth_productivity_index: float   # 0 (idle youth) .. 1 (highly productive)
    education_level: float            # 0 .. 1 (literacy + tertiary blend)


@dataclass(slots=True)
class EntityStability:
    """Stability & risk vector: conflict, unrest, cultural friction."""

    geopolitical_stress: float        # war / conflict probability, 0 .. 1
    societal_unrest_index: float      # 0 calm .. 1 revolt
    religion_cultural_friction: float # 0 cohesive .. 1 fractured


@dataclass(slots=True)
class EntityBlackSwan:
    """Black-swan catalyst vector: climate, supply chain, resource depletion."""

    climate_disaster_exposure: float  # 0 safe .. 1 highly exposed
    supply_chain_resilience: float    # 0 brittle .. 1 robust
    resource_depletion: float         # 0 abundant .. 1 exhausted


class CountryEntity:
    """An autonomous sovereign entity wrapping the rich :class:`CountryState`.

    The entity is the OO behavioural skin over the (serialisation-friendly)
    ``CountryState`` schema. It exposes the *basic* macro metrics as live
    properties bound to the underlying state, augments the state with
    higher-order **demographic / stability / black-swan** vectors, and owns a
    ``calculate_behavior`` matrix describing how the country auto-mutates when
    external shocks strike (e.g. an aging society under a war shock collapses
    consumption and triggers capital flight).

    All mutations are returned as :class:`Adjustment` deltas so they flow through
    the kernel's single simultaneous-apply step (no hidden side effects), keeping
    the tick deterministic and race-free.
    """

    def __init__(self, state: CountryState, rng: random.Random | None = None) -> None:
        self.state = state
        self._rng = rng or random.Random(hash(state.code) & 0xFFFF)
        # Owned, evolving catalysts (not directly mirrored from base state).
        g = state.geopolitical
        self._war_prob = _clamp01(
            0.55 * g.geopolitical_risk + 0.25 * g.election_risk
            + 0.20 * (1.0 - g.political_stability)
        )
        self._religion_friction = _clamp01(
            0.4 * state.labor.gini_coefficient + 0.3 * (1.0 - g.press_freedom)
            + 0.3 * g.social_unrest_index
        )
        self._resource_depletion = _clamp01(
            0.6 * (1.0 - state.industrial.energy_independence)
            + 0.4 * state.industrial.water_stress_index
        )

    # -- identity -------------------------------------------------------------
    @property
    def code(self) -> str:
        return self.state.code

    @property
    def name(self) -> str:
        return self.state.name

    # -- basic macro metrics (live views over the base state) -----------------
    @property
    def gdp(self) -> float:
        return self.state.gdp

    @property
    def gdp_growth(self) -> float:
        return self.state.gdp_growth

    @property
    def inflation(self) -> float:
        return self.state.monetary.inflation_cpi

    @property
    def interest_rate(self) -> float:
        return self.state.monetary.interest_rate

    @property
    def unemployment(self) -> float:
        return self.state.labor.unemployment

    @property
    def treasury_reserves(self) -> float:
        """Sovereign war-chest: FX reserves + central-bank reserves (USD)."""
        return self.state.fiscal.foreign_reserves + self.state.monetary.central_bank_reserves

    # -- higher-order derived vectors -----------------------------------------
    def demographics(self) -> EntityDemographics:
        lab = self.state.labor
        aging = _clamp01((lab.median_age - 20.0) / 50.0)
        youth = _clamp01(
            0.6 * (lab.productivity_index / 150.0) + 0.4 * (1.0 - lab.youth_unemployment)
        )
        education = _clamp01(0.5 * lab.literacy_rate + 0.5 * lab.tertiary_education)
        return EntityDemographics(
            birth_rate=lab.birth_rate,
            aging_index=aging,
            youth_productivity_index=youth,
            education_level=education,
        )

    def stability(self) -> EntityStability:
        return EntityStability(
            geopolitical_stress=self._war_prob,
            societal_unrest_index=self.state.geopolitical.social_unrest_index,
            religion_cultural_friction=self._religion_friction,
        )

    def black_swan(self) -> EntityBlackSwan:
        ind = self.state.industrial
        climate = _clamp01(0.5 * ind.water_stress_index + 0.5 * (1.0 - ind.food_security_index))
        resilience = _clamp01(1.0 - ind.supply_chain_friction)
        return EntityBlackSwan(
            climate_disaster_exposure=climate,
            supply_chain_resilience=resilience,
            resource_depletion=self._resource_depletion,
        )

    # -- catalyst evolution ---------------------------------------------------
    def evolve_catalysts(self, market_stress: float, scale: float) -> None:
        """Slowly drift the owned catalysts (war/friction/depletion) each tick."""
        g = self.state.geopolitical
        # War probability mean-reverts toward the structural geopolitical risk,
        # but market stress and unrest push it up.
        war_target = _clamp01(
            0.55 * g.geopolitical_risk + 0.25 * g.social_unrest_index + 0.20 * market_stress
        )
        self._war_prob += (war_target - self._war_prob) * 0.05 * scale
        self._war_prob = _clamp01(self._war_prob + self._rng.uniform(-0.004, 0.004) * scale)

        friction_target = _clamp01(
            0.4 * self.state.labor.gini_coefficient + 0.3 * (1.0 - g.press_freedom)
            + 0.3 * g.social_unrest_index
        )
        self._religion_friction += (friction_target - self._religion_friction) * 0.04 * scale
        self._religion_friction = _clamp01(self._religion_friction)

        # Resource depletion is monotone-ish: consumption > production depletes it.
        ind = self.state.industrial
        burn = max(0.0, ind.energy_consumption - ind.energy_production) / 100.0
        self._resource_depletion = _clamp01(
            self._resource_depletion + (0.002 * burn + 0.0005) * scale
        )

    # -- crisis detection (feeds the geopolitical causal graph) ----------------
    def detect_crises(self) -> list[CrisisEvent]:
        """Surface active macro-crisis catalysts crossing their trigger thresholds."""
        crises: list[CrisisEvent] = []
        demo = self.demographics()
        bswan = self.black_swan()

        if self._war_prob > 0.6:
            crises.append(CrisisEvent(
                self.code, CrisisType.WAR, self._war_prob,
                f"{self.name}: elevated conflict probability {self._war_prob*100:.0f}%",
            ))
        if self._resource_depletion > 0.7:
            crises.append(CrisisEvent(
                self.code, CrisisType.RESOURCE_SHOCK, self._resource_depletion,
                f"{self.name}: resource depletion at {self._resource_depletion*100:.0f}%",
            ))
        if demo.aging_index > 0.72 and demo.birth_rate < 0.010:
            sev = _clamp01(0.5 * demo.aging_index + 0.5 * (1.0 - demo.youth_productivity_index))
            crises.append(CrisisEvent(
                self.code, CrisisType.DEMOGRAPHIC_AGING, sev,
                f"{self.name}: demographic aging / birth collapse",
            ))
        if self.inflation > 0.25:
            crises.append(CrisisEvent(
                self.code, CrisisType.HYPERINFLATION, _clamp01(self.inflation / 0.5),
                f"{self.name}: inflation {self.inflation*100:.0f}%",
            ))
        if self.state.geopolitical.social_unrest_index > 0.7:
            crises.append(CrisisEvent(
                self.code, CrisisType.CIVIL_UNREST,
                self.state.geopolitical.social_unrest_index,
                f"{self.name}: civil unrest",
            ))
        return crises

    # -- BEHAVIOR MATRIX ------------------------------------------------------
    def calculate_behavior(
        self, market_stress: float, external_shock: float, scale: float
    ) -> list[Adjustment]:
        """Auto-mutate internal state in response to external conditions.

        ``external_shock`` in ``[0, 1]`` is the propagated geopolitical/market
        shock reaching this entity. The matrix couples the entity's structural
        vectors so that, e.g., an aging society (high ``aging_index``) hit by a
        war shock (high ``external_shock``) suffers a **drastic** consumption
        deflation and capital flight far beyond what a young, resilient economy
        would experience under the same shock.
        """
        demo = self.demographics()
        stab = self.stability()
        bswan = self.black_swan()
        adjustments: list[Adjustment] = []

        shock = _clamp01(external_shock)
        stress = _clamp01(market_stress)

        # --- 1) Consumption deflation: amplified by aging + fragility ---------
        # Aging populations cut spending hardest under stress (precautionary
        # saving); youth productivity and education cushion the blow.
        fragility = _clamp01(0.6 * demo.aging_index + 0.4 * (1.0 - bswan.supply_chain_resilience))
        cushion = 0.5 * demo.youth_productivity_index + 0.5 * demo.education_level
        deflation = _clamp01((0.6 * shock + 0.4 * stress) * (0.5 + fragility) * (1.0 - 0.4 * cushion))
        if deflation > 0.01:
            # NOTE: gdp_growth is an ANNUALIZED rate mutated once per sim-day.
            # The old -0.05*deflation shaved up to 5pp of annual growth PER DAY,
            # a self-feeding doom loop (stress -> deflation -> growth -> stress)
            # that no stabilizer could win against. Keep the drag real but small.
            adjustments += [
                Adjustment(self.code, "geopolitical", "consumer_confidence",
                           -0.12 * deflation * scale, reason="consumption_deflation"),
                Adjustment(self.code, "geopolitical", "business_confidence",
                           -0.08 * deflation * scale, reason="demand_contraction"),
                Adjustment(self.code, "", "gdp_growth",
                           -0.008 * deflation * scale, reason="aging_demand_collapse"),
                Adjustment(self.code, "industrial", "capacity_utilization",
                           -0.01 * deflation * scale, reason="idle_capacity"),
            ]

        # --- 2) Capital flight: aging + war + unrest drain the war-chest ------
        flight_drive = _clamp01(
            0.45 * shock + 0.25 * stab.geopolitical_stress
            + 0.20 * stab.societal_unrest_index + 0.10 * demo.aging_index
        )
        if flight_drive > 0.15:
            # Threshold raised + fx push softened: ambient background stress
            # (aging, mild unrest) used to depreciate every currency 2%/day
            # forever — a compounding one-way slide no anchor could offset.
            adjustments += [
                Adjustment(self.code, "fiscal", "foreign_reserves",
                           -0.06 * flight_drive * self.treasury_reserves * scale,
                           reason="capital_flight"),
                Adjustment(self.code, "fiscal", "fdi_inflow",
                           -0.10 * flight_drive * self.state.fiscal.fdi_inflow * scale,
                           reason="fdi_reversal"),
                Adjustment(self.code, "monetary", "fx_spot",
                           self.state.monetary.fx_spot * 0.008 * (flight_drive - 0.15) * scale,
                           reason="currency_depreciation"),
                Adjustment(self.code, "monetary", "yield_10y",
                           0.004 * flight_drive * scale, reason="sovereign_risk_premium"),
            ]

        # --- 3) Resource / supply-chain stress -> imported inflation ---------
        supply_stress = _clamp01(
            0.5 * bswan.resource_depletion + 0.3 * (1.0 - bswan.supply_chain_resilience)
            + 0.2 * shock
        )
        if supply_stress > 0.1:
            adjustments += [
                Adjustment(self.code, "monetary", "inflation_cpi",
                           0.012 * supply_stress * scale, reason="supply_shock_inflation"),
                Adjustment(self.code, "industrial", "supply_chain_friction",
                           0.02 * supply_stress * scale, reason="supply_fragmentation"),
            ]

        # --- 4) Cultural friction + inequality -> unrest feedback ------------
        unrest_push = _clamp01(
            0.5 * stab.religion_cultural_friction * (0.5 + stress)
            + 0.3 * max(0.0, self.inflation - 0.04) * 8.0
            + 0.2 * max(0.0, self.unemployment - 0.06) * 6.0
        )
        if unrest_push > 0.05:
            adjustments += [
                Adjustment(self.code, "geopolitical", "social_unrest_index",
                           0.05 * unrest_push * scale, reason="cultural_friction_unrest"),
                Adjustment(self.code, "geopolitical", "political_stability",
                           -0.03 * unrest_push * scale, reason="legitimacy_erosion"),
            ]

        # --- 5) Climate black-swan: rare physics-only hit (no canned headline)
        disaster_p = bswan.climate_disaster_exposure * 0.002 * scale
        if self._rng.random() < disaster_p:
            hit = _clamp01(0.4 + 0.6 * bswan.climate_disaster_exposure)
            adjustments += [
                Adjustment(self.code, "industrial", "agricultural_output",
                           -6.0 * hit, reason="climate_disaster"),
                Adjustment(self.code, "", "gdp_growth", -0.02 * hit, reason="disaster_output_loss"),
                Adjustment(self.code, "industrial", "food_security_index",
                           -0.05 * hit, reason="crop_failure"),
            ]

        return adjustments

    # -- serialisation --------------------------------------------------------
    def to_dict(self) -> dict:
        demo = self.demographics()
        stab = self.stability()
        bswan = self.black_swan()
        return {
            "code": self.code,
            "name": self.name,
            "basic": {
                "gdp": round(self.gdp, 2),
                "gdp_growth": round(self.gdp_growth, 4),
                "inflation": round(self.inflation, 4),
                "interest_rate": round(self.interest_rate, 4),
                "unemployment": round(self.unemployment, 4),
                "treasury_reserves": round(self.treasury_reserves, 2),
            },
            "demographics": {
                "birth_rate": round(demo.birth_rate, 4),
                "aging_index": round(demo.aging_index, 4),
                "youth_productivity_index": round(demo.youth_productivity_index, 4),
                "education_level": round(demo.education_level, 4),
            },
            "stability": {
                "geopolitical_stress": round(stab.geopolitical_stress, 4),
                "societal_unrest_index": round(stab.societal_unrest_index, 4),
                "religion_cultural_friction": round(stab.religion_cultural_friction, 4),
            },
            "black_swan": {
                "climate_disaster_exposure": round(bswan.climate_disaster_exposure, 4),
                "supply_chain_resilience": round(bswan.supply_chain_resilience, 4),
                "resource_depletion": round(bswan.resource_depletion, 4),
            },
        }


class WorldKernel:
    def __init__(
        self,
        bus: EventBus,
        world: WorldState | None = None,
        models: list[ReactionModel] | None = None,
        agents: list | None = None,  # retained for DI compatibility; unused
        market: MarketContext | None = None,
        narrator: NarrativeEngine | None = None,
        event_probability: float = 0.18,
        max_events_per_tick: int = 6,
        governor_llm_pool=None,
        governor_llm_base_url: str = "",
        governor_llm_model: str = "",
        governor_llm_cadence_ticks: int = 20,
    ) -> None:
        self._bus = bus
        self._world = world or default_world()
        self._models = models or default_models()               # classic macro agents
        self._agents = agents or []  # legacy seam; hierarchy broker owns cognition
        self._market = market or MarketContext()
        self._narrator = narrator or NarrativeEngine(seed=7)
        self._event_p = event_probability
        self._max_events = max_events_per_tick
        self._sim_day = 0
        self._prev_regime: str | None = None
        self._last_micro = MicrostructuralVolatilityVector.neutral()
        # Dual-mode coupling + H200 anti-overfitting domain randomization.
        self._mode = get_mode_manager()
        self._randomizer = DomainRandomizer(low=0.05, high=0.15)
        self._meta_vol_pressure = 0.0
        # Object-oriented entity kernel: one autonomous CountryEntity per nation.
        self._entities: dict[str, CountryEntity] = {
            code: CountryEntity(state) for code, state in self._world.countries.items()
        }
        # Matrix-based geopolitical causal graph (trade / alliance / cultural).
        self._graph = GeopoliticalCausalGraph(self._world)
        # Narrative gate: while a stress episode persists, the SAME agent story
        # (actor+country+tag) would otherwise re-fire every tick with slightly
        # different numbers. Emit it once per cooldown window, unless severity
        # escalates. key -> (sim_day_emitted, severity_rank)
        self._narrative_gate: dict[str, tuple[int, int]] = {}
        # STRUCTURAL ANCHORS: snapshot of ratchet-prone fields at boot. Several
        # dynamics only ever push one way (unrest up, fx down, exports down,
        # agri down after disasters, FDI multiplicatively to 0) — without a
        # recovery force they saturate their clamps and the whole world locks
        # into a permanent doom state (36% unemployment, demand ~= 0).
        self._macro_anchors: dict[str, dict[tuple[str, str], float]] = {}
        _anchor_specs = (
            ("monetary", "fx_spot"),
            ("monetary", "money_supply_m2"),
            ("fiscal", "export_index"),
            ("fiscal", "import_index"),
            ("fiscal", "trade_balance_pct"),
            ("fiscal", "current_account_pct"),
            ("fiscal", "fdi_inflow"),
            ("fiscal", "foreign_reserves"),
            ("industrial", "agricultural_output"),
            ("industrial", "food_security_index"),
            ("industrial", "capacity_utilization"),
            ("industrial", "supply_chain_friction"),
            ("geopolitical", "social_unrest_index"),
            ("geopolitical", "political_stability"),
        )
        for code, cstate in self._world.countries.items():
            anchors: dict[tuple[str, str], float] = {}
            for group, fname in _anchor_specs:
                vec = getattr(cstate, group, None)
                if vec is not None and hasattr(vec, fname):
                    anchors[(group, fname)] = float(getattr(vec, fname))
            self._macro_anchors[code] = anchors
        # PHASE-2 injection queue: anomalies / REST mutations / LLM scenarios.
        self._pending: list[dict] = []
        # Native ECONITH Mesa-style sovereign step kernel (no external scheduler).
        self._mesa_kernel = MesaSovereignKernel()
        # Hierarchical cognitive world (replaces the old hardcoded market-aware
        # ensemble AND the flat agent-cluster). Tier 1 governors (control law or
        # LLM JSON) -> Tier 2 Nash wage bargaining -> Tier 3 six-thousand belief
        # clusters solving continuous consumption/portfolio problems. Scenarios
        # emerge from conflicting objective functions, never scripts.
        self._broker = HierarchyBroker()
        self._last_broker_telemetry = None
        # Tier-1 governors deliberate asynchronously on a slower cognitive
        # cadence. Physics never waits on network I/O: the broker keeps applying
        # the last valid structured directive, with its deterministic control law
        # as the fail-safe whenever the provider is unavailable or returns bad JSON.
        self._governor_llm_pool = governor_llm_pool
        self._governor_llm_base_url = governor_llm_base_url
        self._governor_llm_model = governor_llm_model
        self._governor_llm_cadence = max(1, int(governor_llm_cadence_ticks))
        self._governor_llm_task: asyncio.Task | None = None
        self._governor_llm_status = (
            "ready" if governor_llm_pool is not None else "disabled"
        )
        self._governor_llm_last_tick: int | None = None
        # Material-triggered multi-agent dialogue (enum-first, validated).
        self._dialogue = DialogueOrchestrator(
            valid_codes=tuple(self._world.countries),
            pool=governor_llm_pool,
            base_url=governor_llm_base_url,
            model=governor_llm_model,
            on_directives=self._broker.set_llm_directives,
            cooldown_ticks=15,
        )
        self._last_dialogue_bundle = None
        # TITAN scale-out tensor engine (50 hubs + 100 proxies). Co-steps with the
        # classic entity loop; Sentinel still consumes the EventBus snapshot from
        # the original path. REALITY mode-gates are unchanged.
        try:
            from econith.world.sovereign import SovereignEngine

            self._titan = SovereignEngine()
            self._mesa_kernel.attach_titan(self._titan)
        except Exception:  # noqa: BLE001
            self._titan = None

    # -- introspection --------------------------------------------------------
    @property
    def world(self) -> WorldState:
        return self._world

    def state_dict(self) -> dict:
        base = self._world.to_dict()
        base["entities"] = {code: ent.to_dict() for code, ent in self._entities.items()}
        base["agent_population"] = self._broker.snapshot()
        return base

    def entity(self, code: str) -> CountryEntity | None:
        return self._entities.get(code)

    def country_dict(self, code: str) -> dict | None:
        c = self._world.countries.get(code)
        return c.to_dict() if c else None

    def market_snapshot(self) -> dict:
        return asdict(self._market.snapshot())

    def _sim_date(self) -> str:
        return (SIM_START + timedelta(days=self._sim_day)).isoformat()

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        # World clock.
        self._bus.subscribe("time.tick", self._on_tick)
        # Quant -> World ingestion (build the live MarketContext).
        self._bus.subscribe("ai.signal", self._on_ai_signal)
        self._bus.subscribe("indicator.obi", self._on_obi)
        self._bus.subscribe("indicator.volume_delta", self._on_volume_delta)
        self._bus.subscribe("alt.liquidation", self._on_liquidation)
        self._bus.subscribe("alt.funding_rate", self._on_funding)
        self._bus.subscribe("alt.open_interest", self._on_open_interest)
        self._bus.subscribe("sentinel.status", self._on_sentinel)
        self._bus.subscribe("meta.world.directive", self._on_world_directive)

        n = self._world.countries
        feats = next(iter(n.values())).feature_count_template() if n else 0
        logger.info(
            "unified kernel registered: %d countries x %d features, "
            "%d macro models + hierarchical broker (%d belief clusters, "
            "Tier1 governors + Tier2 bargaining + Tier3 population)",
            len(n), feats, len(self._models), self._broker.micro.n_clusters,
        )

    async def _on_world_directive(self, event: Event) -> None:
        """Core AI scenario pressure — scales outbound micro shock modestly."""
        p = event.payload
        self._meta_vol_pressure = max(
            0.0, min(1.0, float(p.get("volatility_pressure", 0.0) or 0.0))
        )

    # -- Quant -> World ingestion handlers ------------------------------------
    async def _on_ai_signal(self, event: Event) -> None:
        p = event.payload
        self._market.ingest_ai_signal(
            direction=float(p.get("direction", 0.0) or 0.0),
            confidence=float(p.get("confidence", 0.0) or 0.0),
            action=p.get("action", "FLAT"),
            regime=p.get("regime", "CALM"),
            regime_confidence=float(p.get("regime_confidence", 0.0) or 0.0),
        )

    async def _on_obi(self, event: Event) -> None:
        obi = event.payload.get("obi")
        if obi is not None:
            self._market.ingest_obi(float(obi))

    async def _on_volume_delta(self, event: Event) -> None:
        vd = event.payload.get("volume_delta")
        if vd is not None:
            self._market.ingest_volume_delta(float(vd))

    async def _on_liquidation(self, event: Event) -> None:
        self._market.ingest_liquidation(float(event.payload.get("total_notional", 0.0) or 0.0))

    async def _on_funding(self, event: Event) -> None:
        fr = event.payload.get("funding_rate")
        if fr is not None:
            self._market.ingest_funding(float(fr))

    async def _on_open_interest(self, event: Event) -> None:
        oi = event.payload.get("oi_change_pct")
        if oi is not None:
            self._market.ingest_open_interest(float(oi))

    async def _on_sentinel(self, event: Event) -> None:
        self._market.ingest_sentinel(event.payload.get("mode", "NORMAL"))

    # -- external event injection (PHASE 2 producers) -------------------------
    def enqueue_event(self, kind: str, **payload) -> None:
        """Queue an anomaly / mutation / scenario for the next tick's PHASE 2."""
        self._pending.append({"kind": kind, **payload})

    # -- tick loop :: deterministic 5-phase execution -------------------------
    async def _on_tick(self, event: Event) -> None:
        """Run one simulated day as five strictly-ordered, race-free phases.

        PHASE 1 SNAPSHOT -> PHASE 2 APPLY EVENTS -> PHASE 3 RESOLVE CONFLICTS ->
        PHASE 4 UPDATE WORLD -> PHASE 5 EMIT SIGNALS. Each phase fully completes
        before the next begins, so no phase observes a half-mutated world.

        COMPUTE GUARDRAIL: when the operator's *Enable World Simulation* switch
        is OFF the entire agent pipeline suspends — the tick early-returns, state
        is frozen, and no CPU/GPU/RAM is spent so the box runs on standard market
        pipelines only.
        """
        from core.system_controller import get_system_controller

        if not get_system_controller().world_pipeline_active():
            return
        self._sim_day = int(event.payload.get("sim_day", self._sim_day + 1))
        self._world.sim_day = self._sim_day
        multiplier = int(event.payload.get("multiplier", 1))
        scale = min(max(multiplier, 1), 20) ** 0.5  # gentle speed coupling

        ctx = self._phase1_snapshot(scale)
        self._phase2_apply_events(ctx)
        self._phase3_resolve_conflicts(ctx)
        self._phase4_update_world(ctx)
        await self._phase5_emit_signals(ctx)

    # -- PHASE 1 :: SNAPSHOT STATE -------------------------------------------
    def _phase1_snapshot(self, scale: float) -> dict:
        """Freeze the tick's inputs so later phases see a stable world view."""
        sim = self._mode.is_simulation()
        # Do NOT randomize the authoritative World state in-place. Repeated
        # multiplicative perturbations caused a random walk that pushed CPI and
        # other macro variables to their clamps within seconds. Domain
        # randomization belongs in generated training episodes; the interactive
        # runtime only jitters the outbound micro-impact observation in phase 5.
        snap = self._market.snapshot()
        return {
            "sim": sim,
            "scale": scale,
            "market": snap,
            "coupling": macro_to_micro(self._world),   # standing World->Quant bias
            "proposals": [],
            "facts": [],
            "agent_shocks": [],
            "legacy_events": [],
        }

    # -- PHASE 2 :: APPLY EVENTS ---------------------------------------------
    def _phase2_apply_events(self, ctx: dict) -> None:
        """Inject queued anomalies / REST mutations / LLM scenarios + evolve entities."""
        sim, scale = ctx["sim"], ctx["scale"]
        market = ctx["market"]

        # Drain the deterministic pending queue (sorted for reproducibility).
        for ev in sorted(self._pending, key=lambda e: e.get("kind", "")):
            kind = ev.get("kind")
            if kind == "crisis" and sim:
                origin = ev.get("origin", "")
                try:
                    ctype = CrisisType(ev.get("crisis_type", "war"))
                except ValueError:
                    ctype = CrisisType.WAR
                sev = float(ev.get("severity", 0.5))
                shock = self._graph.crisis_micro(CrisisEvent(origin, ctype, sev,
                                                             ev.get("headline", "")))
                ctx["agent_shocks"].append(shock)
            elif kind == "mutation":
                c = self._world.countries.get(ev.get("code", ""))
                if c is not None:
                    c.set_field(ev.get("group", ""), ev.get("field", ""),
                                _clamp_field(ev.get("field", ""), float(ev.get("value", 0.0))))
        self._pending.clear()

        # Evolve every entity's owned catalysts, then let each entity best-respond
        # to the propagated external shock via its behaviour matrix.
        self._graph.rebuild()
        stress = market.stress
        for code, ent in self._entities.items():
            ent.evolve_catalysts(stress, scale)

        # Detect crises and propagate them across the causal graph -> extra shocks
        # + per-entity external-shock intensities used by the behaviour matrix.
        external: dict[str, float] = {code: 0.0 for code in self._entities}
        for code, ent in self._entities.items():
            for crisis in ent.detect_crises():
                reach = self._graph.propagate(crisis.origin, crisis.severity)
                for tgt, intensity in reach.items():
                    external[tgt] = max(external.get(tgt, 0.0), intensity)
                if sim:
                    ctx["agent_shocks"].append(self._graph.crisis_micro(crisis))
        ctx["external"] = external

    # -- PHASE 3 :: RESOLVE CONFLICTS ----------------------------------------
    def _phase3_resolve_conflicts(self, ctx: dict) -> None:
        """Adjudicate competing vectors. The Sentinel ALWAYS wins.

        When the Sentinel has frozen the Quant book (FROZEN), any synthetic
        micro-shock the World would inject into the trading brain is vetoed: a
        halted market cannot be pushed further by simulated macro fear. In
        REDUCE_ONLY the shock is attenuated. This mirrors the sovereignty gate at
        the conflict-resolution layer.
        """
        market = ctx["market"]
        mode = market.sentinel_mode
        if mode == "FROZEN":
            ctx["veto_scale"] = 0.0
            ctx["veto_reason"] = "sentinel_frozen"
        elif mode == "REDUCE_ONLY":
            ctx["veto_scale"] = 0.4
            ctx["veto_reason"] = "sentinel_reduce_only"
        else:
            ctx["veto_scale"] = 1.0
            ctx["veto_reason"] = ""

    # -- PHASE 4 :: UPDATE WORLD ---------------------------------------------
    def _phase4_update_world(self, ctx: dict) -> None:
        """Compute behavioural physics and propagate causal impacts across entities."""
        scale = ctx["scale"]
        snap = ctx["market"]
        external = ctx["external"]
        proposals: list[Adjustment] = ctx["proposals"]
        facts: list[CausalFact] = ctx["facts"]

        # 1) classic macro reaction models (central bank / trade / sentiment).
        for model in self._models:
            for code in self._world.codes():
                proposals.extend(model.react(code, self._world))

        # 2) HIERARCHICAL COGNITIVE WORLD (Tier1 governors -> Tier2 Nash wage
        # bargaining -> Tier3 six-thousand belief clusters). Replaces the old
        # hardcoded market-aware ensemble and the flat cluster. Emergent events
        # and macro injections arise from conflicting objective functions, and
        # the household flows produce the micro-shock fed to the Quant brain.
        br_props, br_facts, br_shock = self._hierarchy_behavior(ctx, snap)
        proposals.extend(br_props)
        facts.extend(br_facts)
        if br_shock is not None:
            ctx["agent_shocks"].append(br_shock)

        # 4) ENTITY BEHAVIOUR MATRIX: each autonomous CountryEntity best-responds
        #    to the propagated external shock (aging + war -> consumption collapse
        #    + capital flight, etc.). Routed through the Mesa scheduler when
        #    available, else the native loop — identical physics either way.
        proposals.extend(self._entity_behavior(external, snap.stress, scale))

        # 5) Quant -> World macro feedback (capital flight, FX, yields, unrest).
        feedback = quant_to_macro(self._world, snap)
        fb_props, fb_facts = self._feedback_to_proposals(feedback)
        proposals.extend(fb_props)
        facts.extend(fb_facts)

        # 6) apply everything simultaneously, then step derived macro.
        ctx["legacy_events"] = self._apply(proposals, scale)
        self._step_derived(scale)

        # 6b) NORMALIZATION LAYER: pull the most drift-prone macro variables back
        # toward economically-coherent anchors so no single one-directional push
        # can saturate its clamp (the failure mode that pinned CPI/yields at the
        # ceiling). Damped mean reversion => the world reaches a steady state.
        self._stabilize_macro(scale)

        # 7) regime-transition narrative (Quant regime flip explained by macro).
        transition = self._regime_transition_fact(snap, ctx["coupling"])
        if transition is not None:
            facts.insert(0, transition)

    def _stabilize_macro(self, scale: float) -> None:
        """Damped mean-reversion of drift-prone macro variables toward anchors.

        Every tick each variable relaxes a fraction ``k`` toward a coherent
        target (inflation -> core/target blend; yields -> policy-rate curve;
        credit -> nominal growth). Combined with the tightened clamps this
        removes the runaway-then-saturate behaviour and yields a steady state
        that still moves with genuine shocks — the reversion is gentle enough
        that a real crisis still shows before it decays.
        """
        k = min(0.25, 0.10 * float(scale))
        # Inflation gets a stronger pull: the legacy reaction models carry a
        # standing inflationary bias that, uncapped, parks headline CPI in the
        # low-teens. A firmer reversion overpowers that drift and settles CPI in
        # a realistic single-digit band around the core/target anchor.
        k_infl = min(0.60, 0.40 * float(scale))
        for c in self._world.countries.values():
            m = getattr(c, "monetary", None)
            if m is None:
                continue
            # Inflation anchors to a blend of the central-bank target and the
            # slower-moving core measure — headline can deviate, not diverge.
            target_infl = getattr(m, "inflation_target", 0.02)
            core = getattr(m, "core_inflation", target_infl)
            anchor_infl = 0.5 * target_infl + 0.5 * core
            m.inflation_cpi = _clamp_field(
                "inflation_cpi", m.inflation_cpi + k_infl * (anchor_infl - m.inflation_cpi)
            )
            # Yield curve reverts to a policy-rate-anchored shape (2y ~ rate+50bps,
            # 10y ~ rate + a term premium that widens with inflation).
            rate = getattr(m, "interest_rate", 0.03)
            if hasattr(m, "yield_2y"):
                y2_anchor = rate + 0.005
                m.yield_2y = _clamp_field(
                    "yield_2y", m.yield_2y + k * (y2_anchor - m.yield_2y)
                )
            if hasattr(m, "yield_10y"):
                term_premium = 0.015 + 0.5 * max(0.0, m.inflation_cpi - anchor_infl)
                y10_anchor = rate + term_premium
                m.yield_10y = _clamp_field(
                    "yield_10y", m.yield_10y + k * (y10_anchor - m.yield_10y)
                )
            # Credit growth reverts toward nominal GDP growth (real growth + CPI).
            if hasattr(m, "credit_growth"):
                nominal = getattr(c, "gdp_growth", 0.02) + m.inflation_cpi
                credit_anchor = max(-0.15, min(0.30, nominal + 0.02))
                m.credit_growth = _clamp_field(
                    "credit_growth", m.credit_growth + k * (credit_anchor - m.credit_growth)
                )

        # STRUCTURAL RECOVERY: ratchet-prone fields (unrest, fx, exports, agri,
        # FDI, stability...) heal slowly toward their boot anchors. The pull is
        # deliberately weak vs shock pushes — a crisis still bites for many sim
        # days — but it removes the one-way absorbing states that previously
        # parked the world at the clamps forever.
        k_struct = min(0.06, 0.02 * float(scale))
        # Social fabric heals faster than trade structure: without a stronger
        # pull, the constant cultural-friction push finds its equilibrium at
        # unrest ~0.7 / stability ~0.25 — a permanent doom mood.
        k_geo = min(0.12, 0.05 * float(scale))
        for code, cstate in self._world.countries.items():
            anchors = self._macro_anchors.get(code)
            if not anchors:
                continue
            for (group, fname), anchor in anchors.items():
                vec = getattr(cstate, group, None)
                if vec is None or not hasattr(vec, fname):
                    continue
                cur = float(getattr(vec, fname))
                if not math.isfinite(cur):
                    setattr(vec, fname, anchor)
                    continue
                k_field = k_geo if group == "geopolitical" else k_struct
                setattr(vec, fname, _clamp_field(
                    fname, cur + k_field * (anchor - cur)))
            # gdp_growth itself gets a weak pull to potential so a shock decays
            # instead of parking at the clamp boundary.
            if math.isfinite(cstate.gdp_growth):
                cstate.gdp_growth += k_struct * (0.02 - cstate.gdp_growth)
            else:
                cstate.gdp_growth = 0.02

    def _entity_behavior(
        self, external: dict[str, float], stress: float, scale: float
    ) -> list[Adjustment]:
        """Collect per-entity behavioural Adjustments for this tick.

        Uses the native ECONITH Mesa-style sovereign step kernel. If anything
        fails, falls back to the direct deterministic loop.
        """
        try:
            props = self._mesa_kernel.step(
                entities=self._entities,
                external=external,
                market_stress=stress,
                scale=scale,
            )
            if props:
                return props
        except Exception:  # noqa: BLE001
            logger.debug("native mesa kernel step failed; direct fallback")
        out: list[Adjustment] = []
        for code, ent in self._entities.items():
            out.extend(ent.calculate_behavior(stress, external.get(code, 0.0), scale))
        return out

    def _hierarchy_behavior(
        self, ctx: dict, snap
    ) -> tuple[list[Adjustment], list[CausalFact], MicrostructuralVolatilityVector | None]:
        """Step the 3-tier cognitive world and project its outputs into Adjustments.

        Tier-1/2/3 run inside :class:`HierarchyBroker`. Macro injections become
        simultaneous Adjustments; threshold-crossing events become CausalFacts
        whose metrics drive the narrative engine (no hardcoded scenario scripts).
        Household deposit/crypto flows become a micro-shock for the Quant brain.
        """
        macro = self._broker_macro_view()
        trade_tension = float(self._world.aggregate().get("trade_tension", 0.0) or 0.0)
        result = self._broker.step(macro, trade_tension=trade_tension, dt=float(ctx["scale"]))
        self._last_broker_telemetry = result.telemetry
        self._maybe_schedule_governor_deliberation(
            macro,
            result.emergent_events,
            tick=result.telemetry.tick,
        )
        # Causal dialogue: only when physics crossed material thresholds.
        try:
            from core.locale_prefs import dashboard_locale

            locale = dashboard_locale()
        except Exception:  # noqa: BLE001
            locale = "en"
        bundle = self._dialogue.maybe_schedule(
            tick=result.telemetry.tick,
            events=result.emergent_events,
            macro=macro,
            locale=locale,
        )
        if bundle is not None:
            self._last_dialogue_bundle = bundle
            ctx["dialogue_bundle"] = bundle

        proposals: list[Adjustment] = []
        for code, deltas in result.macro_injections.items():
            if code not in self._world.countries:
                continue
            country = self._world.countries[code]
            for field_name, delta in deltas.items():
                if abs(delta) < 1e-12:
                    continue
                if field_name == "money_supply_m2_pct":
                    # Fractional M2 change -> absolute USD delta.
                    abs_delta = country.monetary.money_supply_m2 * float(delta)
                    proposals.append(Adjustment(
                        code, "monetary", "money_supply_m2", abs_delta,
                        reason="tier1:money_supply",
                    ))
                    continue
                if field_name == "gdp_growth":
                    proposals.append(Adjustment(
                        code, "", "gdp_growth", float(delta),
                        reason="hierarchy:feedback",
                    ))
                    continue
                group = self._field_group(field_name)
                if group is None:
                    continue
                proposals.append(Adjustment(
                    code, group, field_name, float(delta),
                    reason="hierarchy:injection",
                ))

        facts: list[CausalFact] = []
        # Cap facts per tick so the narrative gate stays legible; pick the most
        # intense events first (they are the ones that actually crossed thresholds).
        ranked_events = sorted(
            result.emergent_events, key=lambda e: e.intensity, reverse=True
        )
        for event in ranked_events[:8]:
            # Broker spans 150 nations; classic WorldState may only own hubs.
            if event.node not in self._world.countries:
                continue
            actor, level = _EMERGENT_META.get(event.kind, ("Household", "info"))
            # Escalate level with intensity for the most severe crossings.
            if event.kind == "safe_haven_migration" and event.intensity > 0.6:
                level = "danger"
            facts.append(CausalFact(
                actor=actor,
                country=event.node,
                action=event.kind,
                cause=f"threshold crossed at intensity {event.intensity:.3f}",
                effect=f"{event.kind} cascade in {event.node}",
                level=level,
                metrics={k: float(v) for k, v in event.metrics.items()},
                tags=(event.kind,),
            ))

        shock: MicrostructuralVolatilityVector | None = None
        mp = result.micro_params
        if mp:
            vol = float(mp.get("volatility_multiplier", 1.0))
            oflow = float(mp.get("order_flow_shock", 0.0))
            drain = float(mp.get("liquidity_drain", 0.0))
            spread = float(mp.get("spread_widening_bps", 0.0))
            if abs(vol - 1.0) > 1e-3 or abs(oflow) > 1e-3 or drain > 1e-3:
                qs = result.quant_state
                shock = MicrostructuralVolatilityVector(
                    volatility_multiplier=vol,
                    order_flow_shock=oflow,
                    liquidity_drain=drain,
                    spread_widening_bps=spread,
                    regime_pressure={
                        "VOLATILE": 1.2 * qs.liquidity_stress + 0.8 * qs.dissatisfaction,
                        "CALM": -1.0 * qs.liquidity_stress - 0.6 * qs.dissatisfaction,
                        "TRENDING": 0.5 * abs(qs.safe_haven_flow),
                        "MEAN_REVERTING": 0.0,
                    },
                    duration_ticks=3,
                    origin="hierarchy:household_flows",
                    headline=(
                        f"Household feedback: haven flow {qs.safe_haven_flow:.2f}, "
                        f"liq stress {qs.liquidity_stress:.2f}, "
                        f"V shock {qs.money_velocity_shock:+.2f}"
                    ),
                )
                # Stash quant state on the context for telemetry.
                ctx["quant_state_input"] = qs.as_vector()
                ctx["quant_state_names"] = qs.names()
        return proposals, facts, shock

    def _maybe_schedule_governor_deliberation(
        self, macro: dict[str, dict[str, float]], events: list, *, tick: int
    ) -> None:
        """Start a real Tier-1 LLM deliberation without blocking the world tick."""
        if self._governor_llm_pool is None:
            return
        if tick % self._governor_llm_cadence != 0:
            return
        if self._governor_llm_task is not None and not self._governor_llm_task.done():
            return

        # Deliberate only for a rotating pair of live sovereigns per cadence.
        # A CPU-bound local 8B model reliably emits two validated JSON rows;
        # asking it for all six at once exceeded the token budget and truncated
        # the object. Non-selected sovereigns keep their control-law policy.
        all_governors = tuple(self._world.countries)
        if not all_governors:
            return
        start = ((tick // self._governor_llm_cadence) * 2) % len(all_governors)
        governors = tuple(
            all_governors[(start + i) % len(all_governors)]
            for i in range(min(2, len(all_governors)))
        )
        self._governor_llm_status = "running"
        self._governor_llm_last_tick = tick
        self._governor_llm_task = asyncio.create_task(
            self._run_governor_deliberation(
                {code: dict(values) for code, values in macro.items()},
                list(events),
                governors,
            ),
            name=f"tier1-governors-{tick}",
        )

    async def _run_governor_deliberation(
        self,
        macro: dict[str, dict[str, float]],
        events: list,
        governors: tuple[str, ...],
    ) -> None:
        source = await self._broker.async_deliberate(
            macro,
            events,
            pool=self._governor_llm_pool,
            base_url=self._governor_llm_base_url,
            model=self._governor_llm_model,
            governors=governors,
        )
        self._governor_llm_status = "active" if source == "llm" else "fallback"
        logger.info(
            "Tier-1 governor deliberation completed: source=%s tick=%s governors=%d",
            source,
            self._governor_llm_last_tick,
            len(governors),
        )

    @staticmethod
    def _field_group(field_name: str) -> str | None:
        """Map a flat macro field onto the CountryState vector group."""
        monetary = {
            "interest_rate", "inflation_cpi", "credit_growth", "velocity_of_money",
            "money_supply_m2", "discount_rate", "yield_10y", "yield_2y",
        }
        fiscal = {
            "corporate_tax", "avg_import_tariff", "public_investment_pct",
            "fdi_inflow", "foreign_reserves",
        }
        geopolitical = {
            "social_unrest_index", "consumer_confidence", "business_confidence",
            "political_stability", "public_approval",
        }
        if field_name in monetary:
            return "monetary"
        if field_name in fiscal:
            return "fiscal"
        if field_name in geopolitical:
            return "geopolitical"
        return None

    def _broker_macro_view(self) -> dict[str, dict[str, float]]:
        """Flatten CountryState into the dense per-nation dict the broker expects."""
        out: dict[str, dict[str, float]] = {}
        for code, c in self._world.countries.items():
            out[code] = {
                "interest_rate": c.monetary.interest_rate,
                "inflation_cpi": c.monetary.inflation_cpi,
                "inflation_target": c.monetary.inflation_target,
                "unemployment": c.labor.unemployment,
                "gdp_growth": c.gdp_growth,
                "gdp": c.gdp,
                "corporate_tax": c.fiscal.corporate_tax,
                "govt_debt_to_gdp": c.fiscal.govt_debt_to_gdp,
                "budget_deficit_pct": c.fiscal.budget_deficit_pct,
                "avg_import_tariff": c.fiscal.avg_import_tariff,
                "money_supply_m2": c.monetary.money_supply_m2,
                "productivity_index": c.labor.productivity_index,
                "labor_cost_index": c.labor.labor_cost_index,
                "union_density": c.labor.union_density,
                "capital_control": 0.0,  # derived below from reserve requirements
                "txn_friction": float(c.industrial.supply_chain_friction),
                "political_stability": c.geopolitical.political_stability,
                "consumer_confidence": c.geopolitical.consumer_confidence,
            }
            # Proxy capital controls from elevated reserve requirements.
            out[code]["capital_control"] = max(
                0.0, min(1.0, (c.monetary.reserve_requirement - 0.10) / 0.25)
            )
        return out

    # -- PHASE 5 :: EMIT SIGNALS ---------------------------------------------
    async def _phase5_emit_signals(self, ctx: dict) -> None:
        """Pack the updated matrix into telemetry + broadcast the micro shock."""
        sim = ctx["sim"]
        veto_scale = ctx.get("veto_scale", 1.0)

        # Superpose the standing coupling with every agent/crisis shock.
        effective = ctx["coupling"]
        for shock in ctx["agent_shocks"]:
            effective = effective.blend(shock)
        if sim:
            effective = self._randomizer.jitter(effective)
        # PHASE-3 verdict: the Sentinel veto attenuates/zeroes the outbound shock.
        if veto_scale < 1.0:
            effective = self._attenuate_shock(effective, veto_scale)
        if self._meta_vol_pressure > 0.0 and sim:
            # Amplify vol / OBI modestly from Core AI world directive.
            boost = 1.0 + 0.75 * self._meta_vol_pressure
            effective = MicrostructuralVolatilityVector(
                volatility_multiplier=min(
                    4.0, 1.0 + (effective.volatility_multiplier - 1.0) * boost
                ),
                order_flow_shock=max(
                    -1.0, min(1.0, effective.order_flow_shock * boost)
                ),
                liquidity_drain=min(1.0, effective.liquidity_drain * boost),
                spread_widening_bps=effective.spread_widening_bps * boost,
                regime_pressure={
                    k: v * boost for k, v in effective.regime_pressure.items()
                },
                duration_ticks=effective.duration_ticks,
                origin=effective.origin,
                headline=effective.headline,
            )
        self._last_micro = effective

        # SOVEREIGNTY: emit the Quant-facing coupling ONLY in SIMULATION and only
        # if the conflict resolver did not fully veto it.
        if sim and veto_scale > 0.0:
            await self._publish_micro_impact(effective)

        # Publish the rich world snapshot + macro-driven quant bias (always).
        await self._publish(effective)

        # Emit the unified event log (narrative facts + legacy + ambient).
        await self._emit_all(ctx["facts"], ctx["legacy_events"])

        # Publish material dialogue turns (fallback sync + any fresher LLM bundle).
        bundle = ctx.get("dialogue_bundle") or self._dialogue.last_bundle
        if bundle is not None and bundle is not getattr(self, "_published_dialogue", None):
            self._published_dialogue = bundle
            await self._bus.publish("world.dialogue.turn", **bundle.as_dict())
            # Also emit lead utterances as agent narratives (grounded text).
            for utt in bundle.utterances[:3]:
                await self._bus.publish(
                    "world.agent.narrative",
                    sim_day=self._sim_day,
                    actor=utt.role,
                    country=utt.country,
                    text=utt.text,
                    level=bundle.level,
                    source="dialogue",
                    locale=utt.locale,
                    metrics=[m.as_dict() for m in utt.metrics],
                    provenance=bundle.source,
                )

    @staticmethod
    def _attenuate_shock(
        vec: MicrostructuralVolatilityVector, factor: float
    ) -> MicrostructuralVolatilityVector:
        """Scale a micro shock toward neutral by ``factor`` in ``[0, 1]``."""
        f = max(0.0, min(1.0, factor))
        return MicrostructuralVolatilityVector(
            volatility_multiplier=1.0 + (vec.volatility_multiplier - 1.0) * f,
            order_flow_shock=vec.order_flow_shock * f,
            liquidity_drain=vec.liquidity_drain * f,
            spread_widening_bps=vec.spread_widening_bps * f,
            regime_pressure={k: v * f for k, v in vec.regime_pressure.items()},
            duration_ticks=vec.duration_ticks,
            origin=vec.origin,
            headline=vec.headline,
        )

    # -- feedback translation -------------------------------------------------
    def _feedback_to_proposals(
        self, feedback: dict[str, MacroFeedback]
    ) -> tuple[list[Adjustment], list[CausalFact]]:
        proposals: list[Adjustment] = []
        facts: list[CausalFact] = []
        if not feedback:
            return proposals, facts

        for code, fb in feedback.items():
            if not fb.is_material():
                continue
            c = self._world.countries[code]
            proposals.extend([
                Adjustment(code, "fiscal", "foreign_reserves",
                           -fb.capital_flight_usd, reason="market_capital_flight"),
                Adjustment(code, "monetary", "fx_spot",
                           c.monetary.fx_spot * fb.fx_depreciation, reason="market_fx"),
                Adjustment(code, "monetary", "yield_10y",
                           fb.yield_shock_bps / 1e4, reason="market_yield"),
                Adjustment(code, "monetary", "inflation_cpi",
                           fb.imported_inflation, reason="imported_inflation"),
                Adjustment(code, "fiscal", "public_investment_pct",
                           fb.investment_drain, reason="investment_drain"),
                Adjustment(code, "geopolitical", "social_unrest_index",
                           fb.unrest_pressure, reason="market_unrest"),
            ])

        # Only narrate the single worst-hit nation to keep the feed legible.
        worst = max(feedback.values(), key=lambda f: f.intensity)
        if worst.is_material():
            c = self._world.countries[worst.code]
            facts.append(CausalFact(
                actor="Market",
                country=worst.code,
                action="transmitted the market rout into the real economy",
                cause=(f"a systemic risk-off episode (intensity {worst.intensity:.2f}) "
                       f"cascading through fragile balance sheets"),
                effect=(f"${worst.capital_flight_usd/1e9:.1f}B fled {c.name}, the "
                        f"currency lost {worst.fx_depreciation*100:.1f}% and 10y "
                        f"yields jumped {worst.yield_shock_bps:.0f}bps"),
                level="danger" if worst.intensity > 0.4 else "warn",
                metrics={"capital_flight_usd": worst.capital_flight_usd,
                         "yield_shock_bps": worst.yield_shock_bps},
                tags=("quant_to_macro", "capital_flight"),
            ))
        return proposals, facts

    def _regime_transition_fact(self, snap, coupling) -> CausalFact | None:
        current = snap.regime
        prev = self._prev_regime
        self._prev_regime = current
        if prev is None or prev == current:
            return None
        driver = coupling.headline or "shifting order-flow imbalance"
        return self._narrator.regime_transition(
            "Global Market", prev, current, driver, snap.regime_confidence
        )

    # -- state mutation -------------------------------------------------------
    def _apply(self, proposals: list[Adjustment], scale: float) -> list[dict]:
        events: list[dict] = []
        for p in proposals:
            if p.delta != 0.0:
                if p.group == "tariff":
                    cur = self._world.tariff(p.code, p.field)
                    self._world.set_tariff(p.code, p.field, cur + p.delta * scale)
                else:
                    c = self._world.countries.get(p.code)
                    if c is not None:
                        if p.group == "":
                            # Top-level fields (gdp_growth, ...) MUST clamp too:
                            # unbounded shock accumulation here was the doom-loop
                            # entry point (gdp_growth -> -37% -> unemployment 36%).
                            setattr(c, p.field, _clamp_field(
                                p.field, getattr(c, p.field) + p.delta * scale))
                        else:
                            cur = c.get_field(p.group, p.field)
                            if cur is not None:
                                c.set_field(p.group, p.field,
                                            _clamp_field(p.field, cur + p.delta * scale))
            if p.event:
                events.append({
                    "country": self._world.countries[p.code].name
                    if p.code in self._world.countries else p.code,
                    "text": p.event,
                    "level": p.event_level,
                    "source": "world",
                    "actor": "",
                })
        return events

    def _step_derived(self, scale: float) -> None:
        for c in self._world.countries.values():
            m, f, lab, g = c.monetary, c.fiscal, c.labor, c.geopolitical
            real_rate = m.interest_rate - m.inflation_cpi
            growth_target = (
                0.02
                + 0.04 * (g.business_confidence - 0.5)
                + 0.6 * f.trade_balance_pct
                - 0.35 * real_rate
                - 0.02 * c.fiscal.avg_import_tariff
                - 0.05 * g.social_unrest_index          # unrest saps output
            )
            growth_target = max(-0.15, min(0.15, growth_target))
            c.gdp_growth += (growth_target - c.gdp_growth) * 0.08 * scale
            c.gdp *= 1.0 + (c.gdp_growth * scale) / 365.0
            if lab.population > 0:
                c.gdp_per_capita = c.gdp / lab.population

            # Okun's law: unemployment drifts opposite to growth.
            u_target = max(0.01, 0.05 - 0.8 * (c.gdp_growth - 0.02))
            lab.unemployment = _clamp_field(
                "unemployment", lab.unemployment + (u_target - lab.unemployment) * 0.05 * scale)

            # average import tariff tracks the matrix row mean
            row = self._world.tariffs.get(c.code, {})
            others = [v for k, v in row.items() if k != c.code]
            if others:
                f.avg_import_tariff = sum(others) / len(others)

    # -- event emission -------------------------------------------------------
    async def _emit_all(self, facts: list[CausalFact], legacy_events: list[dict]) -> None:
        from core.locale_prefs import dashboard_locale

        locale = dashboard_locale()
        severity = {"danger": 4, "warn": 3, "ok": 2, "info": 1}
        ranked = sorted(facts, key=lambda f: severity.get(f.level, 0), reverse=True)

        # Agent feed: highest-severity fact whose story gate is open. While a
        # stress episode persists the same story would re-fire every tick with
        # drifting numbers, so each actor+country+tag story fires once per
        # cooldown window unless its severity escalates.
        for fact in ranked:
            if self._narrative_gate_open(fact, severity, channel="feed"):
                await self._emit_agent_narrative(fact, locale=locale)
                break

        # World "Sự kiện" headline: policy warn/legacy OR top macro fact.
        headline: dict | None = None
        for leg in legacy_events:
            if leg.get("level") in ("warn", "danger") and leg.get("text"):
                headline = leg
                break
        if headline is None:
            for fact in ranked:
                if self._narrative_gate_open(fact, severity, channel="headline"):
                    headline = {
                        "country": fact.country,
                        "text": self._narrator.compose(fact, locale=locale),
                        "level": fact.level,
                        "source": _ACTOR_SOURCE.get(fact.actor, "world"),
                    }
                    break
        if headline:
            await self._bus.publish(
                "world.headline",
                sim_day=self._sim_day,
                country=headline["country"],
                message=headline["text"],
                level=headline.get("level", "info"),
                source=headline.get("source", "world"),
                locale=locale,
            )

    _NARRATIVE_COOLDOWN_DAYS = 45
    _GLOBAL_TAG_COOLDOWN_DAYS = 20

    def _narrative_gate_open(
        self, fact: CausalFact, severity: dict[str, int], *, channel: str
    ) -> bool:
        """True if this actor+country+tag story may be emitted this tick.

        A story that already fired stays muted for a cooldown window of sim
        days unless it comes back with strictly higher severity. Additionally,
        the same *tag* (e.g. labor_strike) may only headline once globally per
        ``_GLOBAL_TAG_COOLDOWN_DAYS`` so the feed does not look like the same
        template stamped across every country.
        """
        tag = fact.tags[0] if getattr(fact, "tags", None) else fact.cause[:32]
        key = f"{channel}|{fact.actor}|{fact.country}|{tag}"
        rank = severity.get(fact.level, 0)
        last = self._narrative_gate.get(key)
        if (
            last is not None
            and self._sim_day - last[0] < self._NARRATIVE_COOLDOWN_DAYS
            and rank <= last[1]
        ):
            return False
        gkey = f"{channel}|tag|{tag}"
        glast = self._narrative_gate.get(gkey)
        if (
            glast is not None
            and self._sim_day - glast[0] < self._GLOBAL_TAG_COOLDOWN_DAYS
            and rank <= glast[1]
        ):
            return False
        self._narrative_gate[key] = (self._sim_day, rank)
        self._narrative_gate[gkey] = (self._sim_day, rank)
        return True

    async def _emit_agent_narrative(self, fact: CausalFact, *, locale: str) -> None:
        text = self._narrator.compose(fact, locale=locale)
        source = _ACTOR_SOURCE.get(fact.actor, "world")
        await self._bus.publish(
            "world.agent.narrative",
            sim_day=self._sim_day,
            actor=fact.actor,
            country=fact.country,
            text=text,
            level=fact.level,
            source=source,
            locale=locale,
        )
        # Regime flips already land on the Event Log via world.headline —
        # publishing system.log here duplicates the same line with a date prefix.
        if fact.level == "danger" and source != "regime":
            message = f"[{self._sim_date()}] {fact.country}: {text}"
            await self._bus.publish(
                "system.log", level=fact.level, source=source, message=message
            )
        await self._bus.publish(
            "world.event",
            sim_day=self._sim_day,
            country=fact.country,
            message=text,
            level=fact.level,
        )

    def _ambient_event(self) -> dict:
        c = random.choice(list(self._world.countries.values()))
        tmpl = random.choice(_AMBIENT)
        text = tmpl.format(
            company=random.choice(_COMPANIES),
            country=c.name,
            dir=random.choice(["stronger", "weaker", "steady"]),
        )
        return {"country": c.name, "text": text, "level": "info", "source": "world"}

    async def _emit_event(self, ev: dict) -> None:
        message = f"[{self._sim_date()}] {ev['country']}: {ev['text']}"
        source = ev.get("source", "world")
        actor = ev.get("actor", "")
        level = ev.get("level", "info")

        if actor in _AGENT_ACTORS or source in ("corporate", "government", "society"):
            await self._bus.publish(
                "world.agent.narrative",
                sim_day=self._sim_day,
                actor=actor or source,
                country=ev["country"],
                text=ev["text"],
                level=level,
                source=source,
            )

        # Routine ambient lines stay out of the dashboard feed. Regime flips are
        # already published via world.headline — skip system.log to avoid twins.
        if source == "regime":
            pass
        elif level == "danger" or source in ("corporate", "government", "society"):
            await self._bus.publish(
                "system.log", level=level, source=source, message=message
            )
        await self._bus.publish(
            "world.event", sim_day=self._sim_day, country=ev["country"],
            message=ev["text"], level=level,
        )

    # -- publication ----------------------------------------------------------
    async def _publish_micro_impact(self, vec: MicrostructuralVolatilityVector) -> None:
        """Push the microstructural shock the Quant engine ingests."""
        await self._bus.publish(
            "world.micro_impact", sim_day=self._sim_day, **vec.model_dump()
        )

    async def _publish(self, micro: MicrostructuralVolatilityVector) -> None:
        aggregate = self._world.aggregate()
        market = self._market.snapshot()
        await self._bus.publish(
            "world.macro",
            sim_day=self._sim_day,
            **{"global": aggregate},
            countries={c: s.to_dict() for c, s in self._world.countries.items()},
            tariffs=self._world.tariffs,
            alliances=self._world.alliances,
            entities={code: ent.to_dict() for code, ent in self._entities.items()},
            agent_population=self._broker.snapshot(),
            hierarchy_telemetry=(
                asdict(self._last_broker_telemetry)
                if self._last_broker_telemetry is not None
                else None
            ),
            governor_llm={
                "enabled": self._governor_llm_pool is not None,
                "status": self._governor_llm_status,
                "model": self._governor_llm_model,
                "cadence_ticks": self._governor_llm_cadence,
                "last_deliberation_tick": self._governor_llm_last_tick,
            },
            dialogue=(
                self._dialogue.last_bundle.as_dict()
                if self._dialogue.last_bundle is not None
                else None
            ),
            micro_impact=micro.model_dump(),
            market=asdict(market),
        )
        bias = -(aggregate["interest_rate"] - 0.025) - max(0.0, aggregate["inflation"] - 0.02)
        bias -= aggregate["trade_tension"]
        # Fold the macro coupling's directional OBI bias into the quant signal.
        bias += 0.5 * micro.order_flow_shock
        quant_payload: dict = {
            "sim_day": self._sim_day,
            "macro_bias": round(bias, 4),
        }
        # Live PPO features from the hierarchy feedback loop (velocity, haven
        # flows, credit impulse, sentiment) — consumed by the Quant brain.
        if self._last_broker_telemetry is not None:
            snap = self._broker.snapshot()
            micro_snap = snap.get("micro", {}) if isinstance(snap, dict) else {}
            quant_payload["hierarchy"] = {
                "tick": snap.get("tick") if isinstance(snap, dict) else None,
                "llm_source": snap.get("llm_source") if isinstance(snap, dict) else None,
                "n_clusters": micro_snap.get("n_clusters"),
                "mean_dissatisfaction": micro_snap.get("mean_dissatisfaction"),
                "demand_index": micro_snap.get("demand_index"),
                "qe_boost": self._last_broker_telemetry.qe_boost,
                "negotiation_rounds": self._last_broker_telemetry.negotiation_rounds,
                "step_ms": round(self._last_broker_telemetry.step_ms, 3),
            }
        await self._bus.publish("world.quant_signal", **quant_payload)

    # -- external mutators (FastAPI / scenario) -------------------------------
    async def mutate_country(self, code: str, group: str, field: str, value: float) -> dict:
        c = self._world.countries.get(code)
        if c is None:
            return {"ok": False, "error": f"unknown country {code}"}
        if group == "" and hasattr(c, field):
            setattr(c, field, _clamp_field(field, float(value)))
            ok = True
        else:
            ok = c.set_field(group, field, _clamp_field(field, float(value)))
        if not ok:
            return {"ok": False, "error": f"unknown field {group}.{field}"}
        text = f"policy set {group + '.' if group else ''}{field} = {value:.4g}"
        await self._emit_event({"country": c.name, "text": text,
                                "level": "warn", "source": "policy"})
        return {"ok": True, "code": code, "group": group, "field": field, "value": value}

    async def set_tariff(self, src: str, dst: str, value: float) -> dict:
        if src not in self._world.countries or dst not in self._world.countries:
            return {"ok": False, "error": "unknown country"}
        prev = self._world.tariff(src, dst)
        self._world.set_tariff(src, dst, value)
        delta = self._world.tariff(src, dst) - prev

        # Immediate domino: target's exports + growth dip; importer inflation up.
        tgt = self._world.countries[dst]
        imp = self._world.countries[src]
        tgt.fiscal.export_index = _clamp_field(
            "export_index", tgt.fiscal.export_index - 40.0 * delta)
        tgt.gdp_growth = _clamp_field("gdp_growth", tgt.gdp_growth - 0.4 * delta)
        imp.monetary.inflation_cpi = _clamp_field(
            "inflation_cpi", imp.monetary.inflation_cpi + 0.15 * delta)

        # A large tariff move is ALSO an immediate microstructural shock the
        # Quant engine feels right away (not just on the next tick).
        if abs(delta) > 0.02:
            shock = MicrostructuralVolatilityVector(
                volatility_multiplier=1.0 + min(3.0, 6.0 * abs(delta)),
                order_flow_shock=-min(0.8, 3.0 * delta),   # tariff hike -> risk-off
                liquidity_drain=min(0.6, 2.0 * abs(delta)),
                spread_widening_bps=40.0 * abs(delta),
                regime_pressure={"VOLATILE": 2.5 * abs(delta), "CALM": -2.0 * abs(delta),
                                 "TRENDING": 0.5 * abs(delta), "MEAN_REVERTING": 0.0},
                duration_ticks=6,
                origin=f"tariff_shock:{src}->{dst}",
                headline=f"{imp.name} tariff shock on {tgt.name}",
            )
            await self._publish_micro_impact(shock)

        verb = "raises" if delta >= 0 else "cuts"
        await self._emit_event({
            "country": imp.name,
            "text": (f"{verb} tariffs on {tgt.name} to {value*100:.0f}% "
                     f"-> {tgt.name} exports -{abs(40.0*delta):.1f} idx, "
                     f"{imp.name} CPI +{max(0.0,0.15*delta)*100:.2f}%"),
            "level": "danger" if delta > 0 else "ok",
            "source": "trade",
        })
        return {"ok": True, "source": src, "target": dst, "value": value}

    def apply_mutations(self, mutations: list[dict]) -> list[str]:
        """Synchronous mutation entry used by the LLM scenario engine.

        ``mutations`` is ``[{country, field, value}]`` with *logical* field
        names (interest_rate / inflation / tax / unemployment / gdp_growth).
        """
        applied: list[str] = []
        for mut in mutations:
            code = mut.get("country", "")
            c = self._world.countries.get(code)
            field = mut.get("field", "")
            value = mut.get("value")
            if c is None or value is None or field not in LOGICAL_FIELDS:
                continue
            group, real = LOGICAL_FIELDS[field]
            if group is None:
                setattr(c, real, _clamp_field(real, float(value)))
            else:
                c.set_field(group, real, _clamp_field(real, float(value)))
            applied.append(f"{c.name}.{field} -> {value}")
        return applied

    async def publish_micro_shock(self, vec: MicrostructuralVolatilityVector) -> None:
        """Public hook so the LLM scenario engine can inject a bespoke shock."""
        self._last_micro = self._last_micro.blend(vec)
        await self._publish_micro_impact(vec)
