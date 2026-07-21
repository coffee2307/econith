"""ECONITH :: econith.world.physics.feedback_loop

The **bottom-up macro/quant feedback engine**. It closes the loop from Tier-3
household decisions back into (a) macro aggregates and (b) the live state vector
the PPO Quant model consumes.

The core mechanism is monetary: when households migrate deposits into the
crypto / safe-haven sleeve, bank reserves shrink, the credit multiplier
contracts, and both the **velocity of money** and the **velocity of
stablecoins** move. Aggregate consumption sets nominal demand, which — against
the (now smaller) effective money stock — repositions the velocity identity
``M · V = P · Y`` and therefore the inflation/liquidity impulses fed back up.

Everything here is a stateful dynamical system with bounded, mean-reverting
deltas so the world cannot random-walk into its clamps (a real failure mode of
the previous engine). Nothing is scripted; the outputs are pure functions of
the household flows plus the engine's own monetary memory.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from econith.world.agents.state_arrays import MicroAggregate

__all__ = ["QuantStateInput", "FeedbackResult", "MacroFeedbackEngine"]


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(slots=True)
class QuantStateInput:
    """Normalized live features injected into the PPO Quant state vector.

    Every field is scaled to roughly ``[-1, 1]`` so the policy network sees a
    stationary observation regardless of absolute USD magnitudes.
    """

    liquidity_stress: float          # >0 as bank liquidity drains
    money_velocity_shock: float      # dV/V of broad money
    stablecoin_velocity: float       # crypto/stable turnover intensity
    safe_haven_flow: float           # normalized deposit->crypto migration
    credit_impulse: float            # loanable-funds expansion (>0) / crunch (<0)
    demand_impulse: float            # aggregate-demand gap
    sentiment: float                 # population mean sentiment
    dissatisfaction: float           # population mean grievance

    def as_vector(self) -> list[float]:
        return [
            self.liquidity_stress,
            self.money_velocity_shock,
            self.stablecoin_velocity,
            self.safe_haven_flow,
            self.credit_impulse,
            self.demand_impulse,
            self.sentiment,
            self.dissatisfaction,
        ]

    @staticmethod
    def names() -> list[str]:
        return [
            "liquidity_stress", "money_velocity_shock", "stablecoin_velocity",
            "safe_haven_flow", "credit_impulse", "demand_impulse",
            "sentiment", "dissatisfaction",
        ]


@dataclass(slots=True)
class FeedbackResult:
    quant_state: QuantStateInput
    macro_deltas: dict[str, float]           # bounded injections to macro state
    micro_params: dict[str, float]           # -> MicrostructuralVolatilityVector
    diagnostics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MacroFeedbackEngine:
    """Stateful monetary/credit accounting that maps flows -> macro + quant."""

    # Reserve/creditary structure.
    reserve_ratio: float = 0.10             # fractional-reserve backing
    stablecoin_decay: float = 0.92          # per-tick decay of the stable float
    velocity_smoothing: float = 0.80        # EMA on measured velocity
    # Internal monetary memory (initialized lazily on first integrate()).
    _bank_deposits: float = 0.0
    _stablecoin_float: float = 0.0
    _velocity_ema: float = 0.0
    _nominal_demand_ema: float = 0.0
    _initialized: bool = False

    def reset(self) -> None:
        self._bank_deposits = 0.0
        self._stablecoin_float = 0.0
        self._velocity_ema = 0.0
        self._nominal_demand_ema = 0.0
        self._initialized = False

    def integrate(
        self,
        aggregate: MicroAggregate,
        *,
        money_supply_m2: float,
        potential_output: float,
        price_level: float = 1.0,
        dt: float = 1.0,
    ) -> FeedbackResult:
        """Fold one tick of household flows into macro + quant state."""
        eps = 1.0
        deposits = max(aggregate.deposit_base_usd, eps)
        if not self._initialized:
            self._bank_deposits = deposits
            self._stablecoin_float = max(aggregate.safe_haven_inflow_usd, eps)
            self._nominal_demand_ema = max(aggregate.consumption_usd, eps)
            self._velocity_ema = self._nominal_demand_ema / max(money_supply_m2, eps)
            self._initialized = True

        # ---- 1) Deposit base & credit multiplier ---------------------------
        outflow = max(0.0, aggregate.deposit_outflow_usd)
        # Deposits fall by migration, partially replenished by fresh saving.
        self._bank_deposits = max(
            eps,
            self._bank_deposits
            - outflow
            + max(0.0, aggregate.saving_flow_usd) * 0.5,
        )
        # Fractional-reserve credit capacity contracts with the deposit base.
        credit_capacity = self._bank_deposits / max(self.reserve_ratio, 1e-3)
        credit_gap = credit_capacity - aggregate.credit_demand_usd
        credit_impulse = _clip(
            credit_gap / max(credit_capacity, eps) - 0.5, -1.0, 1.0
        )

        # ---- 2) Velocity of money (M·V = P·Y identity) ---------------------
        nominal_demand = max(aggregate.consumption_usd, eps)
        self._nominal_demand_ema = (
            self.velocity_smoothing * self._nominal_demand_ema
            + (1.0 - self.velocity_smoothing) * nominal_demand
        )
        effective_money = max(money_supply_m2 - outflow, eps)
        velocity = self._nominal_demand_ema / effective_money
        prev_velocity = max(self._velocity_ema, 1e-6)
        self._velocity_ema = (
            self.velocity_smoothing * self._velocity_ema
            + (1.0 - self.velocity_smoothing) * velocity
        )
        money_velocity_shock = _clip(velocity / prev_velocity - 1.0, -1.0, 1.0)

        # ---- 3) Velocity of stablecoins ------------------------------------
        inflow = max(0.0, aggregate.safe_haven_inflow_usd)
        self._stablecoin_float = max(
            eps, self._stablecoin_float * self.stablecoin_decay + inflow
        )
        stablecoin_velocity = _clip(inflow / self._stablecoin_float, 0.0, 1.0)

        # ---- 4) Liquidity stress & demand gap ------------------------------
        # The demand gap is measured against the population's OWN consumption
        # baseline (consumption_index ~ 1.0), not against GDP — otherwise the
        # household slice is dwarfed by GDP and the impulse pins at -1 forever.
        liquidity_stress = _clip(outflow / deposits * 8.0, 0.0, 1.0)
        demand_impulse = _clip(aggregate.consumption_index - 1.0, -1.0, 1.0)
        safe_haven_flow = _clip(inflow / deposits * 10.0, 0.0, 1.0)

        quant_state = QuantStateInput(
            liquidity_stress=liquidity_stress,
            money_velocity_shock=money_velocity_shock,
            stablecoin_velocity=stablecoin_velocity,
            safe_haven_flow=safe_haven_flow,
            credit_impulse=credit_impulse,
            demand_impulse=demand_impulse,
            sentiment=_clip(aggregate.mean_sentiment, -1.0, 1.0),
            dissatisfaction=_clip(aggregate.mean_dissatisfaction, 0.0, 1.0),
        )

        # ---- 5) Bounded, mean-reverting macro injections -------------------
        # Demand pull + velocity both add to inflation; a credit crunch and
        # deposit flight are disinflationary/contractionary. All deltas are
        # small per tick so the macro state relaxes rather than exploding.
        inflation_delta = _clip(
            0.010 * demand_impulse
            + 0.006 * money_velocity_shock
            - 0.004 * liquidity_stress,
            -0.01, 0.01,
        )
        growth_delta = _clip(
            0.012 * demand_impulse
            + 0.008 * credit_impulse
            - 0.010 * liquidity_stress
            - 0.006 * quant_state.dissatisfaction,
            -0.015, 0.015,
        )
        unrest_delta = _clip(
            0.02 * quant_state.dissatisfaction - 0.01 * max(0.0, quant_state.sentiment),
            -0.02, 0.03,
        )
        macro_deltas = {
            "inflation_cpi": inflation_delta,
            "gdp_growth": growth_delta,
            "social_unrest_index": unrest_delta,
            "credit_growth": _clip(0.02 * credit_impulse, -0.02, 0.02),
            "velocity_of_money": _clip(0.05 * money_velocity_shock, -0.05, 0.05),
        }

        # ---- 6) Microstructure coupling for the Quant brain ----------------
        # Safe-haven inflow is genuine BUY pressure on the crypto tape; broad
        # risk-off (dissatisfaction, liquidity stress) widens spreads and vol.
        micro_params = {
            "volatility_multiplier": _clip(
                1.0 + 1.6 * liquidity_stress + 1.2 * quant_state.dissatisfaction
                + 0.8 * abs(money_velocity_shock),
                1.0, 6.0,
            ),
            "order_flow_shock": _clip(
                0.9 * safe_haven_flow - 0.5 * liquidity_stress
                + 0.3 * demand_impulse,
                -1.0, 1.0,
            ),
            "liquidity_drain": _clip(0.6 * liquidity_stress + 0.3 * safe_haven_flow, 0.0, 1.0),
            "spread_widening_bps": _clip(
                40.0 * liquidity_stress + 15.0 * stablecoin_velocity, 0.0, 120.0
            ),
        }

        diagnostics = {
            "bank_deposits": self._bank_deposits,
            "stablecoin_float": self._stablecoin_float,
            "velocity": velocity,
            "credit_capacity": credit_capacity,
            "effective_money": effective_money,
            "output_gap": aggregate.consumption_usd / max(potential_output, eps),
        }
        return FeedbackResult(
            quant_state=quant_state,
            macro_deltas=macro_deltas,
            micro_params=micro_params,
            diagnostics=diagnostics,
        )
