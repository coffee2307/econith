"""ECONITH :: econith.world.core.hierarchy_broker

The **Hierarchy Broker** — orchestrator of the 3-tier cognitive world.

    Tier 1  Macro-Governors   sovereigns + central banks + media.
                              Continuous reaction functions (Taylor / fiscal
                              gradient) OR structured-JSON directives from an
                              LLM. Their stance injects directly into the macro
                              physics matrix and defines the localized narrative
                              field ``Phi`` that steers Tier-3 beliefs.
    Tier 2  Meso-Strategists  corporate coalitions vs labor unions engage in a
                              *multi-turn Nash wage bargaining* fixed-point,
                              plus friction-driven supply-chain reallocation.
                              Outputs wage growth, employment and cost pressure.
    Tier 3  Micro-Population  6,000 vectorized belief/balance-sheet clusters
                              (see ``state_arrays``) that solve continuous
                              consumption + portfolio problems under hard
                              feasibility constraints.

The broker serializes state between tiers each tick, then folds the resulting
household flows through the physics feedback loop (``feedback_loop``) to update
macro aggregates and emit the live PPO-Quant state vector.

No scenario is ever scripted. Structural shifts (crises, booms, capital flight)
emerge from the *conflict of objective functions* — sovereigns maximizing
stability and revenue, corporations maximizing margin, households maximizing
consumption utility — resolved through the tiers' interacting dynamics.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from time import perf_counter

import numpy as np

from econith.world.agents.state_arrays import (
    Belief,
    EmergentEvent,
    MicroConfig,
    MicroPopulation,
)
from econith.world.physics.feedback_loop import (
    FeedbackResult,
    MacroFeedbackEngine,
    QuantStateInput,
)
from econith.world.sovereign.topology import ALL_CODES, HUB_CODES

logger = logging.getLogger("econith.world.hierarchy")

__all__ = [
    "GovernorDirective",
    "BrokerTelemetry",
    "BrokerResult",
    "HierarchyBroker",
]


# ---------------------------------------------------------------------------
# Macro field defaults — used to build dense arrays from a sparse macro dict.
# ---------------------------------------------------------------------------
_FIELD_DEFAULTS: dict[str, float] = {
    "interest_rate": 0.03,
    "inflation_cpi": 0.025,
    "inflation_target": 0.02,
    "unemployment": 0.05,
    "gdp_growth": 0.02,
    "gdp": 1.0e12,
    "corporate_tax": 0.21,
    "govt_debt_to_gdp": 0.90,
    "budget_deficit_pct": 0.04,
    "avg_import_tariff": 0.03,
    "money_supply_m2": 7.0e11,
    "productivity_index": 100.0,
    "labor_cost_index": 100.0,
    "union_density": 0.15,
    "capital_control": 0.0,
    "txn_friction": 0.05,
    "political_stability": 0.60,
    "consumer_confidence": 0.55,
}


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(slots=True)
class GovernorDirective:
    """A validated Tier-1 directive (from control law or parsed LLM JSON)."""

    code: str
    interest_rate_delta: float = 0.0
    tariff_delta: float = 0.0
    money_supply_delta: float = 0.0     # fractional M2 change
    tax_delta: float = 0.0
    stance: float = 0.0                 # -1 dovish/stimulative .. +1 hawkish/austere
    rationale: str = ""

    def clamped(self) -> "GovernorDirective":
        return GovernorDirective(
            code=self.code,
            interest_rate_delta=_clip(self.interest_rate_delta, -0.02, 0.02),
            tariff_delta=_clip(self.tariff_delta, -0.10, 0.10),
            money_supply_delta=_clip(self.money_supply_delta, -0.05, 0.08),
            tax_delta=_clip(self.tax_delta, -0.03, 0.03),
            stance=_clip(self.stance, -1.0, 1.0),
            rationale=self.rationale[:280],
        )


@dataclass(slots=True)
class BrokerTelemetry:
    tick: int
    llm_source: str
    negotiation_rounds: int
    step_ms: float
    tier1_ms: float
    tier2_ms: float
    tier3_ms: float
    action_dispersion: float
    mean_wage_growth: float
    n_events: int
    demand_index: float = 1.0
    qe_boost: float = 0.0


@dataclass(slots=True)
class BrokerResult:
    macro_injections: dict[str, dict[str, float]]
    emergent_events: list[EmergentEvent]
    quant_state: QuantStateInput
    micro_params: dict[str, float]
    telemetry: BrokerTelemetry
    directives: dict[str, GovernorDirective] = field(default_factory=dict)


class HierarchyBroker:
    """Coordinates Tier-1 policy, Tier-2 negotiation and Tier-3 population."""

    def __init__(
        self,
        *,
        micro_config: MicroConfig | None = None,
        feedback: MacroFeedbackEngine | None = None,
        bargaining_rounds: int = 6,
        wage_elasticity: float = 0.6,
    ) -> None:
        self._codes = ALL_CODES
        self._n = len(self._codes)
        self._hub_codes = HUB_CODES
        self._hub_idx = np.array([self._codes.index(c) for c in self._hub_codes])
        self._micro = MicroPopulation(micro_config)
        self._feedback = feedback or MacroFeedbackEngine()
        self._rounds = int(bargaining_rounds)
        self._wage_elasticity = float(wage_elasticity)
        # Persistent hub wage index — bargaining evolves this toward its
        # equilibrium instead of restarting from 1.0 (which pinned wage growth
        # at the floor every tick). Initialized near marginal product.
        self._wage_index = np.ones(len(self._hub_codes), dtype=np.float64)
        self._last_wage_growth = 0.0
        # Aggregate-demand "heartbeat": the previous tick's consumption index
        # (1.0 == on trend). Tier-1 governors read it to trigger emergency
        # liquidity injection (QE) in a slump before demand can spiral to zero.
        self._last_demand_index = 1.0
        self._last_qe_boost = 0.0
        self._tick = 0
        # Cached LLM directives (refreshed asynchronously; physics runs every
        # tick with whatever stance is current — cognitive coupling without
        # blocking the deterministic loop). Directives EXPIRE: a one-shot
        # decision ("tighten now") must not become a permanent per-tick rate
        # push that ratchets policy to its clamp and never lets go.
        self._llm_directives: dict[str, GovernorDirective] = {}
        self._llm_directives_expire_tick = -1
        # ONE-SHOT GUARD: even within TTL, each cached directive must only move
        # policy once. Re-applying +0.005/tick for 20 ticks was the transient
        # rate-ceiling failure. Track which directive fingerprints have fired.
        self._llm_directives_applied: set[tuple] = set()
        self._llm_source = "control_law"

    @property
    def micro(self) -> MicroPopulation:
        return self._micro

    # -- dense macro array builder -------------------------------------------
    def _field(self, macro: dict[str, dict[str, float]], name: str) -> np.ndarray:
        default = _FIELD_DEFAULTS[name]
        return np.array(
            [float(macro.get(code, {}).get(name, default)) for code in self._codes],
            dtype=np.float64,
        )

    # =====================================================================
    # TIER 1 — Macro-Governors
    # =====================================================================
    def _tier1_control_law(
        self, macro: dict[str, dict[str, float]], trade_tension: float
    ) -> dict[str, GovernorDirective]:
        """Continuous objective-gradient reaction functions per nation.

        Central banks follow a Taylor rule; treasuries follow a debt/growth/
        unrest fiscal gradient. These are control laws, not scenario scripts:
        the *same* function yields easing in a slump and tightening in a boom.
        """
        rate = self._field(macro, "interest_rate")
        infl = self._field(macro, "inflation_cpi")
        target = self._field(macro, "inflation_target")
        growth = self._field(macro, "gdp_growth")
        unrest = np.array(
            [1.0 - float(macro.get(c, {}).get("political_stability", 0.6))
             for c in self._codes]
        )
        debt = self._field(macro, "govt_debt_to_gdp")
        unemployment = self._field(macro, "unemployment")

        # Taylor rule target rate & smoothed adjustment.
        neutral = 0.02
        output_gap = growth - 0.02
        taylor = neutral + 1.5 * (infl - target) + 0.5 * output_gap
        rate_delta = np.clip((taylor - rate) * 0.25, -0.02, 0.02)

        # Fiscal gradient: consolidate when debt/deficit high, loosen when the
        # economy is slack or unrest is rising (revenue vs stability tradeoff).
        tax_delta = np.clip(
            0.01 * (debt - 0.9) + 0.02 * (0.04 - (unemployment - 0.05))
            - 0.03 * unrest,
            -0.03, 0.03,
        )
        # Money growth leans against the rate move (QE when cutting hard).
        money_delta = np.clip(-0.5 * rate_delta - 0.02 * output_gap, -0.05, 0.05)

        # --- Governor policy heartbeat (explicit hardcoded fiscal/monetary rule) ---
        # This is the deterministic strategy the LLM governors later refine:
        #   * demand slump  -> emergency quantitative easing (expand money supply)
        #   * overheating   -> extra tightening on top of the Taylor prescription
        # The QE heartbeat is the safety valve that stops the bottom-up feedback
        # loop from collapsing the whole economy into a deflationary sink.
        demand = float(self._last_demand_index)
        if demand < 0.30:
            qe_boost = 0.05
        elif demand < 0.70:
            qe_boost = 0.02
        else:
            qe_boost = 0.0
        self._last_qe_boost = qe_boost
        money_delta = np.clip(money_delta + qe_boost, -0.05, 0.08)
        # Hardcoded inflation guardrail layered on the Taylor rule: any economy
        # running hot (CPI > 5%) gets an extra +0.5pp policy-rate nudge.
        rate_delta = np.clip(
            np.where(infl > 0.05, rate_delta + 0.005, rate_delta), -0.02, 0.02
        )
        # Tariffs respond to trade tension (defensive) — bounded, symmetric.
        tariff_delta = np.full(
            self._n, float(np.clip(0.04 * (trade_tension - 0.3), -0.05, 0.05))
        )
        # Hawkish/austere stance composite for the media narrative field.
        stance = np.clip(
            2.0 * rate_delta / 0.02 + tax_delta / 0.03 - money_delta / 0.05, -1.0, 1.0
        )

        out: dict[str, GovernorDirective] = {}
        for i, code in enumerate(self._codes):
            out[code] = GovernorDirective(
                code=code,
                interest_rate_delta=float(rate_delta[i]),
                tariff_delta=float(tariff_delta[i]),
                money_supply_delta=float(money_delta[i]),
                tax_delta=float(tax_delta[i]),
                stance=float(stance[i]),
                rationale="taylor+fiscal gradient",
            ).clamped()
        return out

    def _directive_fingerprint(self, d: GovernorDirective) -> tuple:
        return (
            d.code,
            round(d.interest_rate_delta, 6),
            round(d.tariff_delta, 6),
            round(d.money_supply_delta, 6),
            round(d.tax_delta, 6),
        )

    def _blend_directives(
        self, control: dict[str, GovernorDirective]
    ) -> dict[str, GovernorDirective]:
        """LLM directives override the control law where present, valid, FRESH,
        and NOT YET APPLIED. Each unique directive moves policy exactly once;
        subsequent ticks blend LLM stance with the deterministic control law."""
        if self._llm_directives and self._tick > self._llm_directives_expire_tick:
            self._llm_directives = {}
            self._llm_directives_applied = set()
        if not self._llm_directives:
            self._llm_source = "control_law"
            return control
        merged = dict(control)
        for code, directive in self._llm_directives.items():
            if code not in merged:
                continue
            fp = self._directive_fingerprint(directive)
            if fp in self._llm_directives_applied:
                # Already moved policy once — keep LLM stance in the narrative
                # field but do not re-apply its per-tick deltas.
                continue
            merged[code] = directive.clamped()
            self._llm_directives_applied.add(fp)
        self._llm_source = "llm+control_law"
        return merged

    def _narrative_field(
        self, directives: dict[str, GovernorDirective],
        macro: dict[str, dict[str, float]],
    ) -> np.ndarray:
        """Map governor stance + macro into the localized belief target Phi.

        Phi is what Tier-3 beliefs decay toward: a hawkish austere stance under
        high inflation raises inflation-fear and safe-haven pull while eroding
        growth optimism; credible easing during slack lifts optimism and trust.
        """
        phi = np.zeros((self._n, Belief.N), dtype=np.float64)
        infl = self._field(macro, "inflation_cpi")
        target = self._field(macro, "inflation_target")
        growth = self._field(macro, "gdp_growth")
        stability = np.array(
            [float(macro.get(c, {}).get("political_stability", 0.6)) for c in self._codes]
        )
        for i, code in enumerate(self._codes):
            d = directives[code]
            infl_gap = infl[i] - target[i]
            phi[i, Belief.GROWTH_OPTIMISM] = _clip(
                2.0 * growth[i] - 0.6 * max(0.0, d.interest_rate_delta) * 50.0
                + 0.4 * max(0.0, -d.tax_delta) * 30.0, -1.0, 1.0,
            )
            phi[i, Belief.INFLATION_FEAR] = _clip(
                6.0 * max(0.0, infl_gap) + 3.0 * max(0.0, d.tariff_delta)
                - 2.0 * max(0.0, d.interest_rate_delta) * 20.0, -1.0, 1.0,
            )
            phi[i, Belief.SAFE_HAVEN_PULL] = _clip(
                0.6 * phi[i, Belief.INFLATION_FEAR]
                + 0.5 * (1.0 - stability[i])
                - 0.4 * max(0.0, d.interest_rate_delta) * 20.0, -1.0, 1.0,
            )
            phi[i, Belief.JOB_SECURITY] = _clip(
                2.0 * growth[i] - 0.5 * max(0.0, d.interest_rate_delta) * 30.0, -1.0, 1.0,
            )
            phi[i, Belief.INSTITUTIONAL_TRUST] = _clip(
                1.4 * (stability[i] - 0.5) - 0.5 * max(0.0, infl_gap) * 10.0
                - 0.3 * abs(d.stance), -1.0, 1.0,
            )
        return phi

    # =====================================================================
    # TIER 2 — Meso-Strategists (multi-turn Nash wage bargaining)
    # =====================================================================
    def _tier2_negotiate(
        self, macro: dict[str, dict[str, float]], directives: dict[str, GovernorDirective]
    ) -> tuple[dict[str, float], dict[str, float], int]:
        """Iterated Nash bargaining between labor and corporate coalitions.

        Returns per-nation ``income_growth`` and ``job_shock`` plus the number
        of rounds until the wage/employment fixed point converged. Only hub
        nations run the full negotiation; proxies inherit a damped response.
        """
        idx = self._hub_idx
        productivity = self._field(macro, "productivity_index")[idx] / 100.0
        wage = self._wage_index.copy()                 # persistent, evolving
        union = self._field(macro, "union_density")[idx]
        unemployment = self._field(macro, "unemployment")[idx].copy()
        base_unemployment = unemployment.copy()
        infl = self._field(macro, "inflation_cpi")[idx]

        # Firms price at a markup over unit labor cost; the marginal revenue
        # product of labor is what bounds the bargained wage from above.
        markup = 1.15
        price = markup * productivity
        mrp = productivity * price                      # marginal revenue product
        reservation = 0.55 * mrp                        # social wage floor
        rounds_used = self._rounds
        wage_star = wage.copy()

        for r in range(self._rounds):
            # Labor bargaining power rises with unionization and tight labor
            # markets; corporations hold the residual.
            beta_l = np.clip(0.25 + 0.5 * union + 4.0 * (0.06 - unemployment), 0.05, 0.9)
            beta_c = 1.0 - beta_l
            # Nash-bargained wage: interior maximizer of the Nash product,
            # splitting the surplus between MRP (firm ceiling) and reservation.
            new_wage_star = (beta_l * mrp + beta_c * reservation) / (beta_l + beta_c)
            # Labor demand responds to the real wage vs MRP (constant elasticity):
            # a wage below MRP expands hiring, above it sheds jobs.
            employment = (1.0 - base_unemployment) * np.power(
                np.maximum(new_wage_star / np.maximum(mrp, 1e-6), 1e-3),
                -self._wage_elasticity,
            )
            employment = np.clip(employment, 0.5, 1.05)
            new_unemployment = np.clip(1.0 - employment, 0.005, 0.5)
            if np.max(np.abs(new_unemployment - unemployment)) < 1e-4 and r > 0:
                unemployment = new_unemployment
                wage_star = new_wage_star
                rounds_used = r + 1
                break
            unemployment = new_unemployment
            wage_star = new_wage_star

        # Wage adjusts partially toward the bargained target and PERSISTS, so
        # the market reaches a fixed point instead of re-pinning every tick.
        wage_growth_hub = np.clip((wage_star - wage) / np.maximum(wage, 1e-6), -0.15, 0.15)
        self._wage_index = np.clip(wage * (1.0 + 0.5 * wage_growth_hub), 0.3, 5.0)
        # Real per-tick income growth = nominal wage growth minus a SMALL inflation
        # drag. Subtracting the full inflation *level* every tick (as before) made
        # hub real income compound down ~2.5%/tick, collapsing hub demand to ~0.5
        # of trend permanently. Inflation erodes real income only marginally per
        # tick, keeping the bargaining outcome near a real fixed point.
        real_income_growth_hub = np.clip(
            0.5 * wage_growth_hub - 0.05 * infl, -0.03, 0.03
        )
        # job_shock: improvement (>0) or deterioration (<0) in employment vs the
        # macro baseline, transmitted to Tier-3 job-security beliefs.
        job_shock_hub = np.clip(base_unemployment - unemployment, -0.2, 0.2)

        income_growth = {code: 0.0 for code in self._codes}
        job_shock = {code: 0.0 for code in self._codes}
        for k, hub_i in enumerate(idx):
            code = self._codes[hub_i]
            income_growth[code] = float(real_income_growth_hub[k])
            job_shock[code] = float(job_shock_hub[k])
        # Proxies: damped inheritance of their macro growth (no full bargaining).
        # Kept small so income does not compound into a systematic trend that
        # would bias the population's demand index away from ~1.0.
        proxy_growth = self._field(macro, "gdp_growth")
        for i, code in enumerate(self._codes):
            if code not in income_growth or income_growth[code] == 0.0:
                income_growth[code] = float(np.clip(proxy_growth[i] * 0.15, -0.02, 0.02))
        self._last_wage_growth = float(np.mean(wage_growth_hub))
        return income_growth, job_shock, rounds_used

    # =====================================================================
    # ORCHESTRATION
    # =====================================================================
    def step(
        self,
        macro: dict[str, dict[str, float]],
        *,
        trade_tension: float = 0.0,
        dt: float = 1.0,
    ) -> BrokerResult:
        """Advance the full hierarchy one tick and return coupled outputs."""
        t_start = perf_counter()

        # ---- Tier 1 --------------------------------------------------------
        t1 = perf_counter()
        control = self._tier1_control_law(macro, trade_tension)
        directives = self._blend_directives(control)
        phi = self._narrative_field(directives, macro)
        tier1_ms = (perf_counter() - t1) * 1000.0

        # ---- Tier 2 --------------------------------------------------------
        t2 = perf_counter()
        income_growth, job_shock, rounds_used = self._tier2_negotiate(macro, directives)
        tier2_ms = (perf_counter() - t2) * 1000.0

        # ---- Tier 3 --------------------------------------------------------
        t3 = perf_counter()
        policy = self._build_micro_policy(macro, income_growth, job_shock)
        aggregate, events = self._micro.step(narrative_impact=phi, policy=policy, dt=dt)
        tier3_ms = (perf_counter() - t3) * 1000.0

        # ---- Feedback loop -> macro deltas + quant state -------------------
        gdp_total = float(self._field(macro, "gdp").sum())
        m2_total = float(self._field(macro, "money_supply_m2").sum())
        feedback: FeedbackResult = self._feedback.integrate(
            aggregate,
            money_supply_m2=max(m2_total, 1.0),
            potential_output=max(0.65 * gdp_total, 1.0),
            dt=dt,
        )

        macro_injections = self._assemble_injections(directives, feedback, aggregate)

        # Feed this tick's realized demand into the governor heartbeat for next tick.
        self._last_demand_index = float(aggregate.consumption_index)

        stance_vals = np.array([d.stance for d in directives.values()])
        telemetry = BrokerTelemetry(
            tick=self._tick,
            llm_source=self._llm_source,
            negotiation_rounds=rounds_used,
            step_ms=(perf_counter() - t_start) * 1000.0,
            tier1_ms=tier1_ms,
            tier2_ms=tier2_ms,
            tier3_ms=tier3_ms,
            action_dispersion=float(np.std(stance_vals)),
            mean_wage_growth=getattr(self, "_last_wage_growth", 0.0),
            n_events=len(events),
            demand_index=round(float(aggregate.consumption_index), 4),
            qe_boost=round(float(self._last_qe_boost), 4),
        )
        self._tick += 1
        return BrokerResult(
            macro_injections=macro_injections,
            emergent_events=events,
            quant_state=feedback.quant_state,
            micro_params=feedback.micro_params,
            telemetry=telemetry,
            directives=directives,
        )

    def _build_micro_policy(
        self,
        macro: dict[str, dict[str, float]],
        income_growth: dict[str, float],
        job_shock: dict[str, float],
    ) -> dict[str, np.ndarray]:
        rate = self._field(macro, "interest_rate")
        infl = self._field(macro, "inflation_cpi")
        return {
            "real_rate": rate - infl,
            "inflation": infl,
            "unemployment": self._field(macro, "unemployment"),
            "tax": self._field(macro, "corporate_tax"),
            "capital_control": self._field(macro, "capital_control"),
            "txn_friction": self._field(macro, "txn_friction"),
            "income_growth": np.array(
                [income_growth[c] for c in self._codes], dtype=np.float64
            ),
            "job_shock": np.array(
                [job_shock[c] for c in self._codes], dtype=np.float64
            ),
        }

    def _assemble_injections(
        self,
        directives: dict[str, GovernorDirective],
        feedback: FeedbackResult,
        aggregate,
    ) -> dict[str, dict[str, float]]:
        """Merge Tier-1 policy deltas with bottom-up feedback deltas per nation.

        Macro feedback is *global*; it is distributed to nations proportional to
        their household activity so a small open economy is not hit by another's
        deposit flight.
        """
        injections: dict[str, dict[str, float]] = {}
        per_nation = aggregate.per_nation
        for code in self._codes:
            d = directives[code]
            local = per_nation.get(code, {})
            # Local grievance/consumption modulate the global feedback deltas.
            grievance = float(local.get("dissatisfaction", aggregate.mean_dissatisfaction))
            demand_idx = float(local.get("consumption_index", 1.0))
            injections[code] = {
                "interest_rate": d.interest_rate_delta,
                "avg_import_tariff": d.tariff_delta,
                "corporate_tax": d.tax_delta,
                "money_supply_m2_pct": d.money_supply_delta,
                "inflation_cpi": feedback.macro_deltas["inflation_cpi"]
                * (0.5 + 0.5 * demand_idx),
                "gdp_growth": feedback.macro_deltas["gdp_growth"]
                * (0.5 + 0.5 * demand_idx),
                "social_unrest_index": feedback.macro_deltas["social_unrest_index"]
                * (0.5 + grievance),
                "credit_growth": feedback.macro_deltas["credit_growth"],
                "velocity_of_money": feedback.macro_deltas["velocity_of_money"],
            }
        return injections

    # =====================================================================
    # TIER 1 — LLM structured-JSON directive path (optional, non-blocking)
    # =====================================================================
    def build_governor_prompt(
        self, macro: dict[str, dict[str, float]], events: list[EmergentEvent],
        *, governors: tuple[str, ...] | None = None,
    ) -> str:
        """Compact JSON-ready briefing for the Tier-1 LLM governors."""
        govs = governors or self._hub_codes[:12]
        brief = {
            "governors": [
                {
                    "code": c,
                    "interest_rate": round(macro.get(c, {}).get("interest_rate", 0.03), 4),
                    "inflation": round(macro.get(c, {}).get("inflation_cpi", 0.025), 4),
                    "unemployment": round(macro.get(c, {}).get("unemployment", 0.05), 4),
                    "gdp_growth": round(macro.get(c, {}).get("gdp_growth", 0.02), 4),
                    "debt_to_gdp": round(macro.get(c, {}).get("govt_debt_to_gdp", 0.9), 3),
                }
                for c in govs
            ],
            "recent_events": [
                {"node": e.node, "kind": e.kind, "intensity": round(e.intensity, 3)}
                for e in events[:12]
            ],
        }
        return (
            "You are the central banks and treasuries of the listed economies. "
            "Given the macro state and recent emergent events, decide each "
            "governor's policy move to maximize your own objective (stability, "
            "tax revenue, growth). Be terse: rationale <= 8 words. Respond ONLY "
            "with a JSON object of the form "
            '{\"directives\": [{\"code\": str, \"interest_rate_delta\": float, '
            '\"tariff_delta\": float, \"money_supply_delta\": float, '
            '\"tax_delta\": float, \"stance\": float, \"rationale\": str}]}. '
            "Deltas are per-tick and small (|rate|<=0.02, |tariff|<=0.1, "
            "|money|<=0.05, |tax|<=0.03), stance in [-1,1].\n\n"
            + json.dumps(brief, ensure_ascii=False)
        )

    def parse_governor_directives(self, raw: str) -> dict[str, GovernorDirective]:
        """Parse + validate an LLM JSON payload into clamped directives."""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # Small local models sometimes wrap valid JSON in prose/fences.
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end <= start:
                logger.warning("governor JSON parse failed; keeping prior stance")
                return {}
            try:
                payload = json.loads(raw[start:end + 1])
            except (json.JSONDecodeError, TypeError):
                logger.warning("governor JSON parse failed; keeping prior stance")
                return {}
        rows = payload.get("directives", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            return {}
        parsed: dict[str, GovernorDirective] = {}
        valid = set(self._codes)
        for row in rows:
            if not isinstance(row, dict):
                continue
            code = str(row.get("code", "")).upper()
            if code not in valid:
                continue
            try:
                parsed[code] = GovernorDirective(
                    code=code,
                    interest_rate_delta=float(row.get("interest_rate_delta", 0.0)),
                    tariff_delta=float(row.get("tariff_delta", 0.0)),
                    money_supply_delta=float(row.get("money_supply_delta", 0.0)),
                    tax_delta=float(row.get("tax_delta", 0.0)),
                    stance=float(row.get("stance", 0.0)),
                    rationale=str(row.get("rationale", "")),
                ).clamped()
            except (TypeError, ValueError):
                continue
        return parsed

    def set_llm_directives(
        self, directives: dict[str, GovernorDirective], *, ttl_ticks: int = 20
    ) -> None:
        """Install the latest LLM stance; physics uses it on the next tick.

        The stance decays after ``ttl_ticks`` — without an expiry, a single
        "tighten" decision kept adding +0.5pp to the policy rate EVERY tick
        until it pinned at the clamp ceiling (observed live: rate stuck at 25%).
        """
        self._llm_directives = directives or {}
        self._llm_directives_expire_tick = self._tick + max(1, int(ttl_ticks))

    async def async_deliberate(
        self, macro: dict[str, dict[str, float]], events: list[EmergentEvent],
        *, pool, base_url: str, model: str,
        governors: tuple[str, ...] | None = None,
    ) -> str:
        """Call the LLM governor pool and cache the parsed stance.

        Non-blocking with respect to the physics tick: callers run this on a
        slower cadence and ``step`` keeps using the most recent stance.
        """
        import asyncio

        prompt = self.build_governor_prompt(macro, events, governors=governors)

        def _call() -> str:
            response = pool.create_chat_completion(
                base_url=base_url,
                model=model,
                timeout=25.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content":
                     "You are sovereign policy makers. Output strict JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                max_tokens=700,
            )
            return (response.choices[0].message.content or "").strip()

        try:
            raw = await asyncio.to_thread(_call)
        except Exception:  # noqa: BLE001 — LLM path must never break the tick
            logger.exception("governor deliberation failed; keeping control law")
            return "control_law"
        directives = self.parse_governor_directives(raw)
        if directives:
            self.set_llm_directives(directives)
            return "llm"
        return "control_law"

    # -- read model -----------------------------------------------------------
    def snapshot(self) -> dict[str, object]:
        return {
            "tick": self._tick,
            "llm_source": self._llm_source,
            "micro": self._micro.snapshot(),
        }
