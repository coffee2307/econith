"""ECONITH :: econith.world.agents.state_arrays

Tier-3 **Micro-Population** — 6,000 vectorized socio-economic clusters
(150 nations x 40 wealth strata) carrying explicit *belief*, *sentiment* and
*memory* state, and acting inside a **continuous parameterized action space**.

There are no scripted scenarios and no fixed action tokens here. Every cluster
each tick solves two continuous economic problems and is bound by hard balance
sheet feasibility:

  1. Intertemporal consumption  — a CRRA Euler share of cash-on-hand, shifted
     by precautionary (job insecurity) and bring-forward (inflation fear)
     motives, then clipped to a real budget constraint (cash + trust-scaled
     credit line, never below subsistence).
  2. Portfolio reallocation     — a Merton risk share toward the crypto /
     safe-haven sleeve, whose *rebalancing flow* is throttled by transaction
     friction and capital controls, and forbidden outright when liquidity is
     negative (forced deleveraging).

Beliefs evolve with a decay-memory law driven by localized Tier-1 narrative
impact ``Phi``:

    B_i(t+1) = alpha * B_i(t) + (1 - alpha) * Phi_i(t) + epsilon

A slower memory trace ``M`` integrates ``B`` so shocks leave a persistent
imprint. Emergent events (strikes, safe-haven migration, demand swings) are
*never authored*: they fire only when a numeric index crosses a threshold, and
they carry pure numeric metrics — semantic narration is the job of a higher
tier, not a string template down here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import numpy as np

from econith.world.sovereign.topology import ALL_CODES

__all__ = [
    "Belief",
    "ClusterColumn",
    "MicroConfig",
    "EmergentEvent",
    "MicroAggregate",
    "MicroPopulation",
]


# ---------------------------------------------------------------------------
# Belief basis — the semantic axes a cluster forms opinions along.
# ---------------------------------------------------------------------------
class Belief:
    GROWTH_OPTIMISM: Final[int] = 0     # expectation of rising real income
    INFLATION_FEAR: Final[int] = 1      # expectation of eroding purchasing power
    SAFE_HAVEN_PULL: Final[int] = 2     # desire to flee into crypto / hard assets
    JOB_SECURITY: Final[int] = 3        # confidence in keeping income
    INSTITUTIONAL_TRUST: Final[int] = 4  # trust in sovereign / banking system
    N: Final[int] = 5


# ---------------------------------------------------------------------------
# Balance-sheet + action columns (Structure-of-Arrays layout).
# ---------------------------------------------------------------------------
class ClusterColumn:
    POPULATION: Final[int] = 0   # represented head-count
    WEALTH: Final[int] = 1       # real net worth (USD)
    INCOME: Final[int] = 2       # real income per tick (USD)
    DEBT: Final[int] = 3         # outstanding liabilities (USD)
    LIQUIDITY: Final[int] = 4    # liquid cash-on-hand (USD)
    CONSUMPTION: Final[int] = 5  # chosen consumption ratio of cash-on-hand [0,1]
    RISK_ALLOC: Final[int] = 6   # wealth fraction in crypto/safe-haven [0,1]
    DISSATISFACTION: Final[int] = 7  # composite grievance index [0,1]
    N: Final[int] = 8


@dataclass(slots=True)
class MicroConfig:
    n_strata: int = 40                 # wealth bands per nation -> 150*40 = 6000
    memory_retention: float = 0.86     # alpha in the belief update
    memory_trace_retention: float = 0.97  # slow structural memory EMA
    belief_noise: float = 0.015        # epsilon std
    risk_aversion: float = 3.0         # CRRA / Merton gamma
    time_preference: float = 0.98      # beta discount factor
    haven_volatility: float = 0.16     # sigma^2 of the risky sleeve
    subsistence_ratio: float = 0.18    # min consumption vs income
    strike_threshold: float = 0.92     # dissatisfaction that ignites strikes
    haven_migration_threshold: float = 0.05  # flow/deposit that flags migration
    # Consumption is a flow decision: households spend out of INCOME plus a slow
    # annuity draw on their liquid buffer — NOT a fraction of their whole liquid
    # net worth (that produced an unrecoverable dissaving spiral -> demand->0).
    buffer_annuity: float = 0.05       # fraction of liquid buffer drawn per tick
    liquidity_target_mult: float = 2.0  # target buffer = N * income
    liquidity_recycle: float = 0.04    # wage/income redeposit mean-reversion
    baseline_ema: float = 0.05         # rolling reference for the demand index
    seed: int = 20_260_717


@dataclass(slots=True)
class EmergentEvent:
    """A numerically-triggered event. Category is inferred from which index
    crossed a threshold; there is no narrative template — ``metrics`` is the
    machine-readable cause for a higher tier to narrate."""

    node: str
    kind: str
    intensity: float
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class MicroAggregate:
    """Population-level read-model consumed by the physics feedback loop."""

    consumption_usd: float
    saving_flow_usd: float
    safe_haven_inflow_usd: float
    deposit_base_usd: float
    deposit_outflow_usd: float
    credit_demand_usd: float
    mean_sentiment: float
    mean_dissatisfaction: float
    strike_participation: float
    consumption_index: float
    per_nation: dict[str, dict[str, float]] = field(default_factory=dict)


def _clip(a: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip(a, lo, hi)


class MicroPopulation:
    """Vectorized cluster substrate with belief, memory and feasibility gates."""

    def __init__(self, config: MicroConfig | None = None) -> None:
        self._cfg = config or MicroConfig()
        self._codes: tuple[str, ...] = ALL_CODES
        self._n_nodes = len(self._codes)
        self._n_strata = int(self._cfg.n_strata)
        self._rng = np.random.default_rng(self._cfg.seed)

        shape = (self._n_nodes, self._n_strata)
        self._state = np.zeros((*shape, ClusterColumn.N), dtype=np.float64)
        self._belief = np.zeros((*shape, Belief.N), dtype=np.float64)
        self._memory = np.zeros((*shape, Belief.N), dtype=np.float64)
        self._prev_consumption = np.zeros(shape, dtype=np.float64)
        self._prev_dissatisfaction = np.zeros(shape, dtype=np.float64)
        self._baseline_consumption = np.zeros(shape, dtype=np.float64)
        self._tick = 0
        self._seed_population()

    # -- construction ---------------------------------------------------------
    def _seed_population(self) -> None:
        n, s = self._n_nodes, self._n_strata
        # Wealth follows a heavy-tailed lognormal across strata (the top bands
        # hold most of the wealth); income scales sub-linearly with wealth.
        percentile = (np.arange(s) + 0.5) / s                    # (s,)
        wealth_scale = np.exp(2.2 * percentile)                  # convex
        node_scale = self._rng.uniform(0.4, 1.6, size=(n, 1))    # nation size
        wealth = 5.0e10 * node_scale * wealth_scale[None, :]
        income = 0.22 * wealth ** 0.92
        liquidity = 0.35 * income + 0.05 * wealth
        debt = 0.30 * wealth * self._rng.uniform(0.2, 0.8, size=(n, s))

        st = self._state
        st[:, :, ClusterColumn.POPULATION] = (1.0e6 / (n * s))
        st[:, :, ClusterColumn.WEALTH] = wealth
        st[:, :, ClusterColumn.INCOME] = income
        st[:, :, ClusterColumn.DEBT] = debt
        st[:, :, ClusterColumn.LIQUIDITY] = liquidity
        st[:, :, ClusterColumn.CONSUMPTION] = 0.6
        st[:, :, ClusterColumn.RISK_ALLOC] = self._rng.uniform(0.02, 0.12, size=(n, s))

        # Heterogeneous initial beliefs so the population is never a monolith.
        self._belief[:] = self._rng.normal(0.0, 0.12, size=(n, s, Belief.N))
        self._belief[:, :, Belief.JOB_SECURITY] += 0.55
        self._belief[:, :, Belief.INSTITUTIONAL_TRUST] += 0.50
        self._belief[:, :, Belief.GROWTH_OPTIMISM] += 0.35
        self._belief[:] = _clip(self._belief, -1.0, 1.0)
        self._memory[:] = self._belief
        # Baseline demand reference is seeded to the steady flow-consumption the
        # cluster is expected to sustain (income + small buffer draw), and then
        # tracked as a slow EMA in ``step`` so the demand index measures deviation
        # from the population's OWN recent trend rather than a frozen anchor.
        seed_resources = (
            income + self._cfg.buffer_annuity * np.maximum(0.0, liquidity)
        )
        self._baseline_consumption[:] = 0.55 * seed_resources
        self._prev_consumption[:] = self._baseline_consumption
        # Structural floors: real income can trend down for a while under
        # sustained negative bargaining outcomes, but it must never compound to
        # zero — a -0.25%/tick drift over ~18k ticks otherwise annihilates the
        # entire consumption base (index -> 1e-11) with no possible recovery.
        self._income_seed = income.copy()
        self._baseline_seed = self._baseline_consumption.copy()

    # -- introspection --------------------------------------------------------
    @property
    def n_clusters(self) -> int:
        return self._n_nodes * self._n_strata

    def node_index(self, code: str) -> int:
        return self._codes.index(code.upper())

    def belief_snapshot(self, code: str) -> dict[str, float]:
        idx = self.node_index(code)
        b = self._belief[idx].mean(axis=0)
        return {
            "growth_optimism": float(b[Belief.GROWTH_OPTIMISM]),
            "inflation_fear": float(b[Belief.INFLATION_FEAR]),
            "safe_haven_pull": float(b[Belief.SAFE_HAVEN_PULL]),
            "job_security": float(b[Belief.JOB_SECURITY]),
            "institutional_trust": float(b[Belief.INSTITUTIONAL_TRUST]),
        }

    # -- the tick -------------------------------------------------------------
    def step(
        self,
        *,
        narrative_impact: np.ndarray,
        policy: dict[str, np.ndarray],
        dt: float = 1.0,
    ) -> tuple[MicroAggregate, list[EmergentEvent]]:
        """Advance every cluster one tick.

        ``narrative_impact``: (n_nodes, Belief.N) localized Phi from Tier-1
        (media + policy stance). ``policy``: per-nation (n_nodes,) arrays for
        ``real_rate``, ``inflation``, ``unemployment``, ``tax``,
        ``capital_control`` (kappa in [0,1]), ``txn_friction`` (tau in [0,1]),
        ``income_growth`` and ``job_shock`` (from Tier-2 bargaining).
        """
        cfg = self._cfg
        n, s = self._n_nodes, self._n_strata

        # ---- 1) Belief update: decay-memory law with localized narrative ----
        phi = np.broadcast_to(narrative_impact[:, None, :], (n, s, Belief.N))
        eps = self._rng.normal(0.0, cfg.belief_noise, size=(n, s, Belief.N))
        alpha = cfg.memory_retention
        self._belief = _clip(alpha * self._belief + (1.0 - alpha) * phi + eps, -1.0, 1.0)
        # Employment reality overrides talk: unemployment and Tier-2 layoffs
        # erode job security regardless of the media narrative.
        unemployment = policy["unemployment"][:, None]
        job_shock = policy.get("job_shock", np.zeros(n))[:, None]
        self._belief[:, :, Belief.JOB_SECURITY] = _clip(
            self._belief[:, :, Belief.JOB_SECURITY]
            + dt * ((0.05 - unemployment) + 0.5 * job_shock),
            -1.0, 1.0,
        )
        # Slow structural memory integrates the fast belief.
        beta_m = cfg.memory_trace_retention
        self._memory = beta_m * self._memory + (1.0 - beta_m) * self._belief

        b = self._belief
        growth_opt = b[:, :, Belief.GROWTH_OPTIMISM]
        infl_fear = _clip(b[:, :, Belief.INFLATION_FEAR], -1.0, 1.0)
        haven_pull = _clip(b[:, :, Belief.SAFE_HAVEN_PULL], -1.0, 1.0)
        job_sec = _clip(b[:, :, Belief.JOB_SECURITY], -1.0, 1.0)
        trust = _clip(b[:, :, Belief.INSTITUTIONAL_TRUST], -1.0, 1.0)

        st = self._state
        income = st[:, :, ClusterColumn.INCOME]
        wealth = st[:, :, ClusterColumn.WEALTH]
        liquidity = st[:, :, ClusterColumn.LIQUIDITY]

        # ---- 2) Continuous intertemporal consumption (CRRA Euler share) -----
        real_rate = policy["real_rate"][:, None]
        # Effective discount: precautionary saving when jobs feel unsafe;
        # inflation fear pulls consumption forward (spend before money erodes).
        beta_eff = cfg.time_preference * (0.82 + 0.30 * (job_sec * 0.5 + 0.5))
        gross = np.maximum(1.0 + real_rate, 0.05)
        euler = np.power(np.maximum(beta_eff * gross, 1e-6), 1.0 / cfg.risk_aversion)
        consumption_share = 1.0 / (1.0 + euler)
        # Bring-forward / precaution modifiers, kept within (0,1).
        consumption_share = _clip(
            consumption_share
            + 0.12 * np.maximum(0.0, infl_fear)
            - 0.14 * np.maximum(0.0, -job_sec),
            0.05, 0.98,
        )

        cash_on_hand = liquidity + income
        # Spendable resources are a FLOW: full income plus a slow annuity draw on
        # the liquid buffer. Consuming a fraction of the entire liquid stock (which
        # holds ~5% of net worth) caused runaway dissaving that floored liquidity
        # and collapsed demand to ~0 for every nation. This keeps consumption on
        # the order of income so the balance sheet is self-sustaining.
        liquid_buffer = np.maximum(0.0, liquidity)
        spendable = income + cfg.buffer_annuity * liquid_buffer
        # Trust-scaled short-term credit line (feasibility-aware borrowing).
        credit_line = np.maximum(0.0, (trust + 1.0) * 0.5 - 0.30) * income * 2.0
        desired_consumption = consumption_share * spendable
        subsistence = cfg.subsistence_ratio * income
        consumption = np.minimum(desired_consumption, spendable + credit_line)
        consumption = np.maximum(consumption, np.minimum(subsistence, cash_on_hand))
        credit_drawn = np.maximum(0.0, consumption - spendable)
        saving_flow = income - consumption  # may be negative (dissaving)

        # ---- 3) Continuous portfolio choice (Merton) + feasibility gate -----
        # Excess expected real return on the risky sleeve rises with the crowd's
        # safe-haven pull and falls with the real policy rate (cash competes).
        mu_excess = 0.02 + 0.09 * np.maximum(0.0, haven_pull) - 0.6 * real_rate
        target_alloc = _clip(
            mu_excess / (cfg.risk_aversion * cfg.haven_volatility), 0.0, 1.0
        )
        kappa = policy["capital_control"][:, None]     # capital controls
        tau = policy["txn_friction"][:, None]          # transaction friction
        raw_delta = target_alloc - st[:, :, ClusterColumn.RISK_ALLOC]
        # Rebalancing is throttled by friction and controls; illiquid clusters
        # (negative post-consumption liquidity) may only DE-risk, never add.
        post_liquidity = liquidity + saving_flow
        illiquid = post_liquidity < 0.0
        exec_delta = raw_delta * (1.0 - tau) * (1.0 - kappa)
        exec_delta = np.where(illiquid & (exec_delta > 0.0), 0.0, exec_delta)
        new_alloc = _clip(st[:, :, ClusterColumn.RISK_ALLOC] + exec_delta, 0.0, 1.0)
        # Positive reallocation is a real capital flow into the haven sleeve.
        haven_flow = np.maximum(0.0, exec_delta) * wealth
        deposit_outflow = haven_flow  # funded by drawing down bank deposits

        # ---- 4) Balance-sheet roll-forward ----------------------------------
        safe_return = real_rate  # cash/deposits earn the real rate
        risky_return = mu_excess + real_rate  # excess + carry
        portfolio_return = (
            new_alloc * risky_return + (1.0 - new_alloc) * safe_return
        )
        wealth_new = np.maximum(
            0.0, wealth * (1.0 + dt * _clip(portfolio_return, -0.5, 0.5)) + saving_flow
        )
        # Money recycles: income/wages redeposit into the liquid buffer, pulling
        # it back toward a target of a few periods of income. This keeps the
        # velocity of money circulating (spending does not vanish from the system)
        # so liquidity mean-reverts instead of draining monotonically to the floor.
        buffer_target = cfg.liquidity_target_mult * income
        recycle = cfg.liquidity_recycle * (buffer_target - liquidity)
        liquidity_new = np.maximum(
            -0.25 * income,  # a small overdraft floor
            liquidity + saving_flow - deposit_outflow + credit_drawn + dt * recycle,
        )
        # Real income growth is small per tick; clipped tightly so it cannot
        # compound into a runaway trend that biases the whole demand index.
        income_new = income * (
            1.0 + dt * _clip(policy["income_growth"][:, None], -0.05, 0.05)
        )
        # Hard floor + gentle healing toward the seeded structural income so a
        # long recession depresses demand but can never erase the economy.
        income_new = np.maximum(income_new, 0.35 * self._income_seed)
        income_new += 0.01 * dt * np.maximum(0.0, self._income_seed - income_new)

        st[:, :, ClusterColumn.WEALTH] = wealth_new
        st[:, :, ClusterColumn.LIQUIDITY] = liquidity_new
        st[:, :, ClusterColumn.INCOME] = income_new
        st[:, :, ClusterColumn.CONSUMPTION] = consumption / np.maximum(cash_on_hand, 1.0)
        st[:, :, ClusterColumn.RISK_ALLOC] = new_alloc

        # ---- 5) Dissatisfaction index (grievance) ---------------------------
        disslike = _clip(
            0.30 * np.maximum(0.0, infl_fear)
            + 0.28 * np.maximum(0.0, -job_sec)
            + 0.24 * np.maximum(0.0, -trust)
            + 0.30 * np.maximum(0.0, unemployment - 0.05) * 8.0
            - 0.22 * np.maximum(0.0, growth_opt),
            0.0, 1.0,
        )
        rising = disslike - self._prev_dissatisfaction
        st[:, :, ClusterColumn.DISSATISFACTION] = disslike

        # ---- 6) Emergent, threshold-triggered events (nation aggregated) ----
        events = self._detect_events(
            consumption=consumption,
            deposit_outflow=deposit_outflow,
            dissatisfaction=disslike,
            rising=rising,
        )

        aggregate = self._aggregate(
            consumption=consumption,
            saving_flow=saving_flow,
            haven_flow=haven_flow,
            deposit_outflow=deposit_outflow,
            credit_drawn=credit_drawn,
            dissatisfaction=disslike,
        )

        # Roll the demand baseline as a slow EMA of realized consumption so the
        # index re-centers on the population's own trend (a transient shock shows
        # as contraction/expansion, then relaxes back — no permanent 0% attractor).
        b_ema = cfg.baseline_ema
        self._baseline_consumption = np.maximum(
            (1.0 - b_ema) * self._baseline_consumption + b_ema * consumption,
            0.25 * self._baseline_seed,
        )
        self._prev_consumption = consumption
        self._prev_dissatisfaction = disslike
        self._tick += 1
        return aggregate, events

    # -- feasibility helpers (exposed for testing) ----------------------------
    def feasible_consumption(
        self, desired: np.ndarray, cash_on_hand: np.ndarray, credit_line: np.ndarray,
        subsistence: np.ndarray,
    ) -> np.ndarray:
        c = np.minimum(desired, cash_on_hand + credit_line)
        return np.maximum(c, np.minimum(subsistence, cash_on_hand))

    # -- internals ------------------------------------------------------------
    def _detect_events(
        self,
        *,
        consumption: np.ndarray,
        deposit_outflow: np.ndarray,
        dissatisfaction: np.ndarray,
        rising: np.ndarray,
    ) -> list[EmergentEvent]:
        cfg = self._cfg
        pop = self._state[:, :, ClusterColumn.POPULATION]
        events: list[EmergentEvent] = []

        node_pop = pop.sum(axis=1)
        # Population-weighted grievance per nation.
        weighted_dissat = (dissatisfaction * pop).sum(axis=1) / np.maximum(node_pop, 1.0)
        node_rising = (rising * pop).sum(axis=1) / np.maximum(node_pop, 1.0)
        node_consumption = consumption.sum(axis=1)
        node_baseline = np.maximum(self._baseline_consumption.sum(axis=1), 1.0)
        consumption_index = node_consumption / node_baseline
        deposit_base = np.maximum(
            (self._state[:, :, ClusterColumn.LIQUIDITY]
             + self._state[:, :, ClusterColumn.WEALTH]
             * (1.0 - self._state[:, :, ClusterColumn.RISK_ALLOC])).sum(axis=1),
            1.0,
        )
        migration_ratio = deposit_outflow.sum(axis=1) / deposit_base

        for i, code in enumerate(self._codes):
            if weighted_dissat[i] > cfg.strike_threshold and node_rising[i] > 0.0:
                events.append(EmergentEvent(
                    node=code,
                    kind="labor_strike",
                    intensity=float(weighted_dissat[i]),
                    metrics={
                        "dissatisfaction": float(weighted_dissat[i]),
                        "momentum": float(node_rising[i]),
                        "consumption_index": float(consumption_index[i]),
                    },
                ))
            if migration_ratio[i] > cfg.haven_migration_threshold:
                events.append(EmergentEvent(
                    node=code,
                    kind="safe_haven_migration",
                    intensity=float(min(1.0, migration_ratio[i] / 0.1)),
                    metrics={
                        "flow_usd": float(deposit_outflow[i].sum()),
                        "deposit_ratio": float(migration_ratio[i]),
                    },
                ))
            # Only surface material demand breaks (not mild 75–85% drift spam).
            if consumption_index[i] < 0.70:
                events.append(EmergentEvent(
                    node=code, kind="demand_contraction",
                    intensity=float(min(1.0, (0.70 - consumption_index[i]) / 0.3)),
                    metrics={"consumption_index": float(consumption_index[i])},
                ))
            elif consumption_index[i] > 1.25:
                events.append(EmergentEvent(
                    node=code, kind="demand_expansion",
                    intensity=float(min(1.0, (consumption_index[i] - 1.25) / 0.3)),
                    metrics={"consumption_index": float(consumption_index[i])},
                ))
        return events

    def _aggregate(
        self,
        *,
        consumption: np.ndarray,
        saving_flow: np.ndarray,
        haven_flow: np.ndarray,
        deposit_outflow: np.ndarray,
        credit_drawn: np.ndarray,
        dissatisfaction: np.ndarray,
    ) -> MicroAggregate:
        pop = self._state[:, :, ClusterColumn.POPULATION]
        total_pop = float(pop.sum()) or 1.0
        sentiment = (
            0.5 * self._belief[:, :, Belief.GROWTH_OPTIMISM]
            + 0.3 * self._belief[:, :, Belief.JOB_SECURITY]
            + 0.2 * self._belief[:, :, Belief.INSTITUTIONAL_TRUST]
            - 0.4 * np.maximum(0.0, self._belief[:, :, Belief.INFLATION_FEAR])
        )
        deposit_base = float(
            (self._state[:, :, ClusterColumn.LIQUIDITY]
             + self._state[:, :, ClusterColumn.WEALTH]
             * (1.0 - self._state[:, :, ClusterColumn.RISK_ALLOC])).sum()
        )
        strike_mask = dissatisfaction > self._cfg.strike_threshold

        per_nation: dict[str, dict[str, float]] = {}
        node_consumption = consumption.sum(axis=1)
        node_baseline = np.maximum(self._baseline_consumption.sum(axis=1), 1.0)
        for i, code in enumerate(self._codes):
            per_nation[code] = {
                "consumption_usd": float(node_consumption[i]),
                "consumption_index": float(node_consumption[i] / node_baseline[i]),
                "safe_haven_inflow_usd": float(haven_flow[i].sum()),
                "sentiment": float((sentiment[i] * pop[i]).sum() / max(pop[i].sum(), 1.0)),
                "dissatisfaction": float(
                    (dissatisfaction[i] * pop[i]).sum() / max(pop[i].sum(), 1.0)
                ),
                "strike_participation": float(
                    (pop[i] * strike_mask[i]).sum() / max(pop[i].sum(), 1.0)
                ),
            }

        return MicroAggregate(
            consumption_usd=float(consumption.sum()),
            saving_flow_usd=float(saving_flow.sum()),
            safe_haven_inflow_usd=float(haven_flow.sum()),
            deposit_base_usd=deposit_base,
            deposit_outflow_usd=float(deposit_outflow.sum()),
            credit_demand_usd=float(credit_drawn.sum()),
            mean_sentiment=float((sentiment * pop).sum() / total_pop),
            mean_dissatisfaction=float((dissatisfaction * pop).sum() / total_pop),
            strike_participation=float((pop * strike_mask).sum() / total_pop),
            consumption_index=float(node_consumption.sum() / node_baseline.sum()),
            per_nation=per_nation,
        )

    # -- dashboard read model -------------------------------------------------
    def snapshot(self, top_n: int = 10) -> dict[str, object]:
        pop = self._state[:, :, ClusterColumn.POPULATION]
        dissat = self._state[:, :, ClusterColumn.DISSATISFACTION]
        node_dissat = (dissat * pop).sum(axis=1) / np.maximum(pop.sum(axis=1), 1.0)
        order = np.argsort(node_dissat)[::-1][:top_n]
        mean_belief = self._belief.mean(axis=(0, 1))
        recent_consumption = float(self._prev_consumption.sum())
        baseline_total = float(np.maximum(self._baseline_consumption.sum(), 1.0))
        demand_index = recent_consumption / baseline_total
        return {
            "tick": self._tick,
            "n_clusters": self.n_clusters,
            "represented_agents": int(round(float(pop.sum()))),
            "mean_dissatisfaction": round(float(node_dissat.mean()), 4),
            "mean_risk_alloc": round(
                float(self._state[:, :, ClusterColumn.RISK_ALLOC].mean()), 4
            ),
            "demand_index": round(demand_index, 4),
            "beliefs": {
                "growth_optimism": round(float(mean_belief[Belief.GROWTH_OPTIMISM]), 4),
                "inflation_fear": round(float(mean_belief[Belief.INFLATION_FEAR]), 4),
                "safe_haven_pull": round(float(mean_belief[Belief.SAFE_HAVEN_PULL]), 4),
                "job_security": round(float(mean_belief[Belief.JOB_SECURITY]), 4),
                "institutional_trust": round(
                    float(mean_belief[Belief.INSTITUTIONAL_TRUST]), 4
                ),
            },
            "hotspots": [
                {"code": self._codes[i], "dissatisfaction": round(float(node_dissat[i]), 4)}
                for i in order
            ],
        }
