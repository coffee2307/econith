"""ECONITH :: ai.simulator_engine.cross_impact

The **bidirectional cross-impact engine** -- the mathematical heart of the
ECONITH "wow" factor: a closed feedback loop between macro geopolitics
(ECONITH World) and market microstructure (ECONITH Quant).

Two directions, two pure translators:

1. ``macro_to_micro`` -- **World -> Quant**. Reads the aggregate macro/geo state
   (trade tension, inflation, real rates, civil unrest, sanctions, sovereign
   fragility) and emits a :class:`MicrostructuralVolatilityVector`: the shock
   signature the Quant engine ingests. It biases order-flow imbalance (OBI),
   multiplies realised volatility, drains liquidity, widens spreads and -- most
   importantly -- exerts *regime pressure* that can force an HMM/GMM transition
   (e.g. a 200% tariff barrier shoves the market into a High-Volatility regime).

2. ``quant_to_macro`` -- **Quant -> World**. Reads the live
   :class:`~ai.simulator_engine.market_context.MarketSnapshot` (AI conviction,
   realised vol, liquidation cascades, sell pressure) and computes per-country
   :class:`MacroFeedback`: capital flight, currency depreciation, sovereign-yield
   blowout, investment drain, imported inflation and civil-unrest pressure --
   weighted by each nation's structural fragility.

Both functions are side-effect free. The kernel owns application and I/O.
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ai.regime.classifier import REGIMES
from ai.simulator_engine.macro_vectors import CountryState, WorldState
from ai.simulator_engine.market_context import MarketSnapshot

__all__ = [
    "MicrostructuralVolatilityVector",
    "MacroFeedback",
    "GeoAggregate",
    "aggregate_geo",
    "macro_to_micro",
    "quant_to_macro",
    "DomainRandomizer",
    "CrisisType",
    "CrisisEvent",
    "GeopoliticalCausalGraph",
    "crisis_to_micro",
    "HUB_CODES",
]

# The six sovereign hub economies whose shocks propagate across the graph.
HUB_CODES: tuple[str, ...] = ("USA", "CHN", "VNM", "JPN", "IND", "DEU")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ===========================================================================
#  Microstructural Volatility Vector (the World -> Quant shock signature)
# ===========================================================================
class MicrostructuralVolatilityVector(BaseModel):
    """Structured shock the Quant engine ingests off ``world.micro_impact``.

    All fields are additive/multiplicative *biases* on the perceived market
    microstructure, not absolute values -- the Quant engine composes them onto
    its live feed.
    """

    volatility_multiplier: float = 1.0        # >=1 amplifies realised vol
    order_flow_shock: float = 0.0             # signed OBI bias in [-1, 1]
    liquidity_drain: float = 0.0              # 0 (deep) .. 1 (evaporated)
    spread_widening_bps: float = 0.0          # additive half-spread in bps
    regime_pressure: dict[str, float] = Field(default_factory=dict)  # per-REGIME log-bias
    duration_ticks: int = 1                   # decay horizon
    origin: str = "world"                     # provenance for the narrative log
    headline: str = ""                        # one-line human summary

    # -- composition ----------------------------------------------------------
    @staticmethod
    def neutral() -> "MicrostructuralVolatilityVector":
        return MicrostructuralVolatilityVector(
            regime_pressure={r: 0.0 for r in REGIMES}
        )

    def blend(self, other: "MicrostructuralVolatilityVector") -> "MicrostructuralVolatilityVector":
        """Superpose two shocks (multiplicative vol, additive everything else)."""
        rp = {r: self.regime_pressure.get(r, 0.0) + other.regime_pressure.get(r, 0.0)
              for r in REGIMES}
        return MicrostructuralVolatilityVector(
            volatility_multiplier=self.volatility_multiplier * other.volatility_multiplier,
            order_flow_shock=_clamp(self.order_flow_shock + other.order_flow_shock, -1.0, 1.0),
            liquidity_drain=_clamp(self.liquidity_drain + other.liquidity_drain, 0.0, 1.0),
            spread_widening_bps=self.spread_widening_bps + other.spread_widening_bps,
            regime_pressure=rp,
            duration_ticks=max(self.duration_ticks, other.duration_ticks),
            origin=other.origin if other.origin != "world" else self.origin,
            headline=other.headline or self.headline,
        )

    def decayed(self, ticks: int = 1) -> "MicrostructuralVolatilityVector":
        """Exponential relaxation of the transient shock toward neutral."""
        remaining = self.duration_ticks - ticks
        if remaining <= 0:
            return MicrostructuralVolatilityVector.neutral()
        k = remaining / max(1, self.duration_ticks)
        return MicrostructuralVolatilityVector(
            volatility_multiplier=1.0 + (self.volatility_multiplier - 1.0) * k,
            order_flow_shock=self.order_flow_shock * k,
            liquidity_drain=self.liquidity_drain * k,
            spread_widening_bps=self.spread_widening_bps * k,
            regime_pressure={r: v * k for r, v in self.regime_pressure.items()},
            duration_ticks=remaining,
            origin=self.origin,
            headline=self.headline,
        )

    def is_active(self) -> bool:
        return (
            abs(self.volatility_multiplier - 1.0) > 1e-3
            or abs(self.order_flow_shock) > 1e-3
            or self.liquidity_drain > 1e-3
            or any(abs(v) > 1e-3 for v in self.regime_pressure.values())
        )


# ===========================================================================
#  Macro feedback (the Quant -> World consequence per country)
# ===========================================================================
@dataclass(slots=True)
class MacroFeedback:
    """Per-country macro consequences of a market episode."""

    code: str
    capital_flight_usd: float = 0.0     # FDI + reserves fleeing (USD)
    fx_depreciation: float = 0.0        # fractional currency move (>0 weaker)
    yield_shock_bps: float = 0.0        # 10y sovereign yield blowout (bps)
    investment_drain: float = 0.0       # public_investment_pct delta (<0)
    imported_inflation: float = 0.0     # inflation_cpi delta (>0)
    unrest_pressure: float = 0.0        # social_unrest_index delta (>0)
    intensity: float = 0.0              # 0..1 composite severity

    def is_material(self) -> bool:
        return self.intensity > 0.02


# ===========================================================================
#  Geo aggregation (pop-weighted geopolitical stress not in WorldState.aggregate)
# ===========================================================================
@dataclass(slots=True, frozen=True)
class GeoAggregate:
    trade_tension: float
    inflation: float
    real_rate: float
    growth: float
    unrest: float
    geopolitical_risk: float
    sanctions_exposure: float
    business_confidence: float


def aggregate_geo(world: WorldState) -> GeoAggregate:
    """Population-weighted macro/geo aggregates driving the micro shock."""
    states = list(world.countries.values())
    total_pop = sum(s.labor.population for s in states) or 1.0

    def wavg(getter) -> float:
        return sum(getter(s) * s.labor.population for s in states) / total_pop

    macro = world.aggregate()  # gdp_growth, inflation, interest_rate, trade_tension...
    real_rate = macro["interest_rate"] - macro["inflation"]
    return GeoAggregate(
        trade_tension=macro["trade_tension"],
        inflation=macro["inflation"],
        real_rate=real_rate,
        growth=macro["gdp_growth"],
        unrest=wavg(lambda s: s.geopolitical.social_unrest_index),
        geopolitical_risk=wavg(lambda s: s.geopolitical.geopolitical_risk),
        sanctions_exposure=wavg(lambda s: s.geopolitical.sanctions_exposure),
        business_confidence=wavg(lambda s: s.geopolitical.business_confidence),
    )


# ===========================================================================
#  Direction 1 :: World -> Quant
# ===========================================================================
def macro_to_micro(world: WorldState) -> MicrostructuralVolatilityVector:
    """Translate the aggregate macro/geo state into a microstructure shock."""
    geo = aggregate_geo(world)

    # Realised-vol amplifier: tension, unrest, hot inflation and geo-risk all
    # widen the conditional variance the market prices in.
    vol_mult = _clamp(
        1.0
        + 3.4 * geo.trade_tension
        + 2.2 * geo.unrest
        + 1.6 * max(0.0, geo.inflation - 0.03)
        + 1.3 * geo.geopolitical_risk,
        1.0, 8.0,
    )

    # Order-flow imbalance bias: risk-off (tension/inflation) => net selling;
    # strong growth + confidence => net buying. Signed, saturating.
    obi_bias = _clamp(
        -(2.0 * geo.trade_tension + 1.4 * max(0.0, geo.inflation - 0.03) + 1.1 * geo.unrest)
        + (2.0 * geo.growth + 0.6 * (geo.business_confidence - 0.5)),
        -1.0, 1.0,
    )

    # Liquidity evaporates as tension, unrest and sanctions fragment markets.
    liq_drain = _clamp(
        0.55 * geo.trade_tension + 0.45 * geo.unrest + 0.40 * geo.sanctions_exposure,
        0.0, 1.0,
    )

    # Spreads widen with both the vol regime and the liquidity hole.
    spread_bps = 1.0 * (vol_mult - 1.0) + 30.0 * liq_drain

    # Regime pressure: additive log-bias applied to the Quant regime scores.
    # A hot macro state shoves probability mass toward VOLATILE / TRENDING and
    # away from CALM -- this is what "forces an HMM/GMM transition".
    excess_vol = vol_mult - 1.0
    regime_pressure = {
        "VOLATILE": 1.25 * excess_vol + 1.5 * liq_drain,
        "TRENDING": 1.6 * max(0.0, abs(obi_bias) - 0.25) / max(1.0, vol_mult) ,
        "MEAN_REVERTING": 0.25,
        "CALM": -1.4 * excess_vol - 0.8 * liq_drain,
    }

    headline = (
        f"Macro coupling: tension {geo.trade_tension*100:.0f}%, unrest "
        f"{geo.unrest:.2f} -> vol x{vol_mult:.2f}, OBI bias {obi_bias:+.2f}, "
        f"liquidity drain {liq_drain*100:.0f}%"
    )
    return MicrostructuralVolatilityVector(
        volatility_multiplier=round(vol_mult, 4),
        order_flow_shock=round(obi_bias, 4),
        liquidity_drain=round(liq_drain, 4),
        spread_widening_bps=round(spread_bps, 2),
        regime_pressure={k: round(v, 4) for k, v in regime_pressure.items()},
        duration_ticks=1,   # standing coupling (recomputed every tick)
        origin="macro_coupling",
        headline=headline,
    )


# ===========================================================================
#  Direction 2 :: Quant -> World
# ===========================================================================
def _fragility(c: CountryState) -> float:
    """Structural vulnerability of a country to capital flight, in ``[0, 1]``.

    High external debt / weak reserves, low sovereign rating, high sanctions
    exposure, negative trade balance and low political stability all raise it.
    """
    fis, geo = c.fiscal, c.geopolitical
    reserve_cover = _clamp(fis.foreign_reserves / max(1.0, 0.15 * c.gdp), 0.0, 1.0)
    return _clamp(
        0.30 * (1.0 - fis.sovereign_rating)
        + 0.22 * geo.sanctions_exposure
        + 0.18 * (1.0 - reserve_cover)
        + 0.15 * max(0.0, -fis.current_account_pct * 5.0)
        + 0.15 * (1.0 - geo.political_stability),
        0.0, 1.0,
    )


def quant_to_macro(
    world: WorldState,
    market: MarketSnapshot,
) -> dict[str, MacroFeedback]:
    """Map a market episode onto per-country macro consequences.

    Only fires materially during genuine stress (high realised vol + persistent
    selling / liquidation cascades). The systemic shock is then distributed
    across countries in proportion to their structural fragility, so fragile
    emerging economies bleed capital while reserve-currency issuers absorb it.
    """
    # Systemic severity of the market episode.
    systemic = _clamp(
        0.5 * market.stress
        + 0.3 * market.sell_pressure
        + 0.2 * market.liquidation,
        0.0, 1.0,
    )
    # A calm, risk-on tape produces no feedback.
    if systemic < 0.12:
        return {}

    feedback: dict[str, MacroFeedback] = {}
    for code, c in world.countries.items():
        frag = _fragility(c)
        # Capital flees fragile nations hardest; safe havens can even attract.
        directed = systemic * (0.35 + 1.15 * frag)
        intensity = _clamp(directed, 0.0, 1.0)
        if intensity < 0.04:
            continue

        # Capital flight scales with the country's mobile external capital base.
        mobile_base = c.fiscal.fdi_inflow + 0.25 * c.fiscal.foreign_reserves
        capital_flight = mobile_base * 0.18 * intensity

        fb = MacroFeedback(
            code=code,
            capital_flight_usd=capital_flight,
            fx_depreciation=0.06 * intensity,                 # up to ~6% per episode
            yield_shock_bps=120.0 * intensity * frag,         # sovereign dump
            investment_drain=-0.02 * intensity,               # public_investment_pct
            imported_inflation=0.010 * intensity,             # FX pass-through
            unrest_pressure=0.05 * intensity,                 # social_unrest_index
            intensity=round(intensity, 4),
        )
        feedback[code] = fb
    return feedback


# ===========================================================================
#  Domain Randomization (H200-scale anti-overfitting regularizer)
# ===========================================================================
# Curated macro fields to scramble each tick. Format: (group | "", field).
# ``""`` addresses a top-level CountryState attribute (e.g. gdp_growth).
_RANDOMIZED_FIELDS: tuple[tuple[str, str], ...] = (
    ("", "gdp_growth"),
    ("monetary", "interest_rate"),
    ("monetary", "inflation_cpi"),
    ("monetary", "fx_spot"),
    ("fiscal", "trade_balance_pct"),
    ("labor", "unemployment"),
    ("geopolitical", "business_confidence"),
    ("geopolitical", "consumer_confidence"),
    ("geopolitical", "social_unrest_index"),
    ("geopolitical", "political_stability"),
)


class DomainRandomizer:
    """Stochastic domain-randomization noise injector for SIMULATION training.

    Continuous RL on a *deterministic* synthetic coupling overfits: the policy
    memorises the exact ``macro_to_micro`` mapping instead of learning robust,
    transferable structural contingencies. To break that, every simulation tick
    we scramble the synthetic macro variables (and jitter the resulting shock
    vector) by a random multiplicative factor drawn from a ``[low, high]``
    variance band with a random sign -- i.e. each field is perturbed by roughly
    +/-5% to +/-15% per tick.

    This is used ONLY in SIMULATION_MODE and is completely inert in REALITY_MODE
    (the real data plane is never randomized).
    """

    def __init__(
        self,
        low: float = 0.05,
        high: float = 0.15,
        seed: int | None = None,
    ) -> None:
        if not 0.0 <= low <= high:
            raise ValueError("require 0 <= low <= high for the variance band")
        self.low = low
        self.high = high
        self.rng = random.Random(seed)

    # -- primitive ------------------------------------------------------------
    def sample_factor(self) -> float:
        """A multiplicative perturbation ``1 +/- U(low, high)`` (random sign)."""
        magnitude = self.rng.uniform(self.low, self.high)
        sign = 1.0 if self.rng.random() < 0.5 else -1.0
        return 1.0 + sign * magnitude

    def sample_bias(self, scale: float = 1.0) -> float:
        """A signed additive perturbation in ``[-high, +high] * scale``."""
        return self.rng.uniform(-self.high, self.high) * scale

    # -- macro state randomization -------------------------------------------
    def randomize_world(
        self,
        world: WorldState,
        clamp_field: Callable[[str, float], float] | None = None,
    ) -> int:
        """Scramble the curated macro fields of every country, in place.

        ``clamp_field(field, value) -> value`` is supplied by the kernel so the
        perturbed values stay inside their physical bounds. Returns the number of
        field mutations applied (useful for telemetry / tests).
        """
        mutations = 0
        for c in world.countries.values():
            for group, fld in _RANDOMIZED_FIELDS:
                if group == "":
                    if not hasattr(c, fld):
                        continue
                    cur = getattr(c, fld)
                    if not isinstance(cur, (int, float)):
                        continue
                    new = float(cur) * self.sample_factor()
                    if clamp_field is not None:
                        new = clamp_field(fld, new)
                    setattr(c, fld, new)
                    mutations += 1
                else:
                    cur = c.get_field(group, fld)
                    if cur is None or not isinstance(cur, (int, float)):
                        continue
                    new = float(cur) * self.sample_factor()
                    if clamp_field is not None:
                        new = clamp_field(fld, new)
                    if c.set_field(group, fld, new):
                        mutations += 1
        return mutations

    # -- shock-vector jitter --------------------------------------------------
    def jitter(
        self, vec: "MicrostructuralVolatilityVector"
    ) -> "MicrostructuralVolatilityVector":
        """Return a domain-randomized copy of a micro shock vector.

        Perturbs each channel of the shock the RL agent observes so it cannot
        overfit to a single deterministic coupling signature.
        """
        vol_mult = max(1.0, vec.volatility_multiplier * self.sample_factor())
        obi = _clamp(vec.order_flow_shock + self.sample_bias(0.10), -1.0, 1.0)
        liq = _clamp(vec.liquidity_drain * self.sample_factor(), 0.0, 1.0)
        spread = max(0.0, vec.spread_widening_bps * self.sample_factor())
        rp = {
            r: v + self.sample_bias(0.15)
            for r, v in vec.regime_pressure.items()
        }
        return MicrostructuralVolatilityVector(
            volatility_multiplier=round(vol_mult, 4),
            order_flow_shock=round(obi, 4),
            liquidity_drain=round(liq, 4),
            spread_widening_bps=round(spread, 2),
            regime_pressure={k: round(v, 4) for k, v in rp.items()},
            duration_ticks=vec.duration_ticks,
            origin=vec.origin,
            headline=vec.headline,
        )


# ===========================================================================
#  Geopolitical Causal Graph (matrix-based macro-crisis propagation)
# ===========================================================================
from enum import Enum  # noqa: E402 - kept local to this section for cohesion


class CrisisType(str, Enum):
    """Canonical macro-crisis catalysts recognised by the causal graph."""

    WAR = "war"                        # armed conflict / geopolitical rupture
    RESOURCE_SHOCK = "resource_shock"  # energy / rare-earth / food supply cut
    DEMOGRAPHIC_AGING = "demographic_aging"   # structural aging / birth collapse
    HYPERINFLATION = "hyperinflation"  # currency debasement
    SOVEREIGN_DEFAULT = "sovereign_default"   # debt / reserves failure
    TRADE_RUPTURE = "trade_rupture"    # tariff wall / embargo
    CIVIL_UNREST = "civil_unrest"      # societal instability
    PANDEMIC = "pandemic"              # health / labour shock


@dataclass(slots=True)
class CrisisEvent:
    """A macro crisis originating at one country, to be propagated + translated."""

    origin: str
    kind: CrisisType
    severity: float                    # 0..1 intensity at the origin
    headline: str = ""

    def clamped(self) -> "CrisisEvent":
        return CrisisEvent(self.origin, self.kind, _clamp(self.severity, 0.0, 1.0), self.headline)


# How each crisis type shapes the microstructural shock signature. Values are
# per-unit-severity coefficients; ``vol_sign`` < 0 means the crisis *compresses*
# realised volatility (structural stagnation) rather than amplifying it.
_CRISIS_MICRO_PROFILE: dict[CrisisType, dict[str, float]] = {
    #                         vol      obi     liq    spread   VOL    TREND   MREV    CALM
    CrisisType.WAR:            {"vol": 3.2, "obi": -0.75, "liq": 0.55, "spread": 42.0,
                                "VOLATILE": 2.4, "TRENDING": 1.4, "MEAN_REVERTING": -0.4, "CALM": -2.6},
    CrisisType.RESOURCE_SHOCK: {"vol": 2.8, "obi": -0.55, "liq": 0.48, "spread": 36.0,
                                "VOLATILE": 2.1, "TRENDING": 1.6, "MEAN_REVERTING": -0.3, "CALM": -2.2},
    CrisisType.TRADE_RUPTURE:  {"vol": 2.2, "obi": -0.60, "liq": 0.40, "spread": 30.0,
                                "VOLATILE": 1.8, "TRENDING": 0.9, "MEAN_REVERTING": 0.0, "CALM": -1.9},
    CrisisType.HYPERINFLATION: {"vol": 2.4, "obi": -0.40, "liq": 0.35, "spread": 28.0,
                                "VOLATILE": 1.7, "TRENDING": 1.8, "MEAN_REVERTING": -0.5, "CALM": -1.7},
    CrisisType.SOVEREIGN_DEFAULT: {"vol": 3.0, "obi": -0.70, "liq": 0.60, "spread": 45.0,
                                "VOLATILE": 2.3, "TRENDING": 1.0, "MEAN_REVERTING": -0.2, "CALM": -2.4},
    CrisisType.CIVIL_UNREST:   {"vol": 1.8, "obi": -0.45, "liq": 0.30, "spread": 22.0,
                                "VOLATILE": 1.5, "TRENDING": 0.4, "MEAN_REVERTING": 0.2, "CALM": -1.4},
    CrisisType.PANDEMIC:       {"vol": 2.0, "obi": -0.50, "liq": 0.45, "spread": 26.0,
                                "VOLATILE": 1.6, "TRENDING": 0.8, "MEAN_REVERTING": 0.0, "CALM": -1.6},
    # Aging is structural STAGNATION: compressed ranges, thin liquidity, mean
    # reversion dominates. Negative ``vol`` compresses realised volatility (<1x).
    CrisisType.DEMOGRAPHIC_AGING: {"vol": -0.9, "obi": -0.12, "liq": 0.25, "spread": 6.0,
                                "VOLATILE": -1.2, "TRENDING": -0.6, "MEAN_REVERTING": 1.8, "CALM": 0.6},
}


def crisis_to_micro(
    event: CrisisEvent, edge_weight: float = 1.0
) -> MicrostructuralVolatilityVector:
    """Translate a (propagated) macro crisis into a microstructural shock vector.

    ``edge_weight`` in ``[0, 1]`` is how strongly this crisis reaches the market
    node being priced (1.0 at the origin, decaying across graph edges). WAR and
    RESOURCE_SHOCK amplify volatility and push VOLATILE/TRENDING regimes;
    DEMOGRAPHIC_AGING compresses volatility and pushes MEAN_REVERTING.
    """
    ev = event.clamped()
    s = ev.severity * _clamp(edge_weight, 0.0, 1.0)
    prof = _CRISIS_MICRO_PROFILE.get(ev.kind, _CRISIS_MICRO_PROFILE[CrisisType.WAR])

    if prof["vol"] >= 0.0:
        vol_mult = _clamp(1.0 + prof["vol"] * s, 1.0, 8.0)
    else:
        # Compression: 1x down toward ~0.55x as severity rises (thin, ranged tape).
        vol_mult = _clamp(1.0 + prof["vol"] * s, 0.55, 1.0)

    obi = _clamp(prof["obi"] * s, -1.0, 1.0)
    liq = _clamp(prof["liq"] * s, 0.0, 1.0)
    spread = max(0.0, prof["spread"] * s)
    regime_pressure = {
        "VOLATILE": prof["VOLATILE"] * s,
        "TRENDING": prof["TRENDING"] * s,
        "MEAN_REVERTING": prof["MEAN_REVERTING"] * s,
        "CALM": prof["CALM"] * s,
    }
    # War/resource ruptures echo longer than routine coupling.
    duration = 6 if ev.kind in (CrisisType.WAR, CrisisType.RESOURCE_SHOCK,
                                CrisisType.SOVEREIGN_DEFAULT) else 4
    headline = ev.headline or (
        f"{ev.kind.value.replace('_', ' ').title()} @ {ev.origin} "
        f"(sev {ev.severity:.2f}, reach {edge_weight:.2f})"
    )
    return MicrostructuralVolatilityVector(
        volatility_multiplier=round(vol_mult, 4),
        order_flow_shock=round(obi, 4),
        liquidity_drain=round(liq, 4),
        spread_widening_bps=round(spread, 2),
        regime_pressure={k: round(v, 4) for k, v in regime_pressure.items()},
        duration_ticks=duration,
        origin=f"crisis:{ev.kind.value}:{ev.origin}",
        headline=headline,
    )


class GeopoliticalCausalGraph:
    """Matrix-based causal graph over the sovereign world.

    Edges are built from three real-world dependency channels, each carrying a
    normalised transmission weight in ``[0, 1]``:

      * **Trade dependency** -- how exposed B is to a shock at A, proxied by the
        tariff-linkage and relative economic gravity (``gdp`` share).
      * **Military / alliance** -- the alliance/trust matrix: close allies import
        each other's geopolitical stress, rivals partially decouple (or benefit).
      * **Cultural proxy** -- shared-continent + soft-power adjacency, a slow
        transmission channel for societal/ideological contagion.

    A shock injected at a hub propagates outward with multiplicative decay along
    the strongest path (bounded BFS), so a war in one hub biases its trade
    partners and allies far more than distant, weakly-linked economies.
    """

    def __init__(
        self,
        world: WorldState,
        trade_w: float = 0.5,
        alliance_w: float = 0.35,
        cultural_w: float = 0.15,
        decay: float = 0.6,
        max_depth: int = 3,
    ) -> None:
        self._world = world
        self._trade_w = trade_w
        self._alliance_w = alliance_w
        self._cultural_w = cultural_w
        self._decay = _clamp(decay, 0.05, 0.95)
        self._max_depth = max(1, max_depth)
        self._adj: dict[str, dict[str, float]] = {}
        self.rebuild()

    # -- construction ---------------------------------------------------------
    def rebuild(self) -> None:
        """(Re)compute the directed edge-weight matrix from the current world."""
        codes = self._world.codes()
        total_gdp = sum(self._world.countries[c].gdp for c in codes) or 1.0
        adj: dict[str, dict[str, float]] = {a: {} for a in codes}
        for a in codes:
            ca = self._world.countries[a]
            for b in codes:
                if a == b:
                    continue
                cb = self._world.countries[b]
                # Trade: B's exposure to A grows with A's economic gravity and the
                # tariff linkage in either direction (barriers concentrate risk).
                gravity = ca.gdp / total_gdp
                tariff_link = 0.5 * (self._world.tariff(a, b) + self._world.tariff(b, a))
                trade = _clamp(gravity + 1.5 * tariff_link, 0.0, 1.0)
                # Alliance/military: trust in [0,1]; close allies transmit stress.
                alliance = _clamp(self._world.alliance(a, b), 0.0, 1.0)
                # Cultural: same-continent adjacency + mutual soft power.
                same_cont = 1.0 if ca.continent == cb.continent else 0.0
                soft = 0.5 * (ca.geopolitical.soft_power_index + cb.geopolitical.soft_power_index)
                cultural = _clamp(0.6 * same_cont + 0.4 * soft, 0.0, 1.0)

                w = (self._trade_w * trade
                     + self._alliance_w * alliance
                     + self._cultural_w * cultural)
                adj[a][b] = _clamp(w, 0.0, 1.0)
        self._adj = adj

    def edge(self, a: str, b: str) -> float:
        return self._adj.get(a, {}).get(b, 0.0)

    # -- propagation ----------------------------------------------------------
    def propagate(self, origin: str, severity: float = 1.0) -> dict[str, float]:
        """Return each country's received shock intensity from ``origin``.

        Bounded best-path BFS with multiplicative edge decay: reach[origin] == 1
        and every other node gets the strongest attenuated path intensity found
        within ``max_depth`` hops, scaled by ``severity``.
        """
        if origin not in self._adj:
            return {}
        sev = _clamp(severity, 0.0, 1.0)
        reach: dict[str, float] = {origin: 1.0}
        frontier: list[tuple[str, float, int]] = [(origin, 1.0, 0)]
        while frontier:
            node, intensity, depth = frontier.pop()
            if depth >= self._max_depth:
                continue
            for nbr, w in self._adj.get(node, {}).items():
                transmitted = intensity * w * self._decay
                if transmitted <= 0.01:
                    continue
                if transmitted > reach.get(nbr, 0.0):
                    reach[nbr] = transmitted
                    frontier.append((nbr, transmitted, depth + 1))
        return {k: round(v * sev, 4) for k, v in reach.items()}

    # -- crisis -> micro across the graph -------------------------------------
    def crisis_micro(self, event: CrisisEvent) -> MicrostructuralVolatilityVector:
        """Propagate a crisis across the graph and fold it into ONE market shock.

        The market prices the *aggregate* geopolitical fracture: the origin shock
        plus every attenuated echo reaching its trade/alliance/cultural
        neighbours, superposed into a single microstructural vector.
        """
        reach = self.propagate(event.origin, event.severity)
        if not reach:
            return MicrostructuralVolatilityVector.neutral()
        blended = MicrostructuralVolatilityVector.neutral()
        for code, weight in reach.items():
            node_event = CrisisEvent(code, event.kind, event.severity, event.headline)
            blended = blended.blend(crisis_to_micro(node_event, edge_weight=weight))
        # Superposition compounds multiplicatively; clamp to institutional bounds
        # so a graph-wide fracture stays a strong-but-tradeable shock, not a NaN.
        aging = event.kind is CrisisType.DEMOGRAPHIC_AGING
        blended.volatility_multiplier = round(
            _clamp(blended.volatility_multiplier, 0.55 if aging else 1.0, 6.0), 4
        )
        blended.order_flow_shock = round(_clamp(blended.order_flow_shock, -1.0, 1.0), 4)
        blended.liquidity_drain = round(_clamp(blended.liquidity_drain, 0.0, 1.0), 4)
        blended.spread_widening_bps = round(min(blended.spread_widening_bps, 120.0), 2)
        blended.regime_pressure = {
            k: round(_clamp(v, -6.0, 6.0), 4) for k, v in blended.regime_pressure.items()
        }
        blended.origin = f"causal_graph:{event.kind.value}:{event.origin}"
        blended.headline = event.headline or (
            f"{event.kind.value.replace('_', ' ').title()} at {event.origin} "
            f"propagated to {len(reach) - 1} economies"
        )
        return blended
