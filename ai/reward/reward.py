"""ECONITH :: ai.reward.reward

Mega-fund multi-objective reward with explicit anti-greed / reward-hacking
mitigation (master plan, Phase 2, Step 3 + Research 4).

The shaped per-step reward is:

    R_t = clip(w1 * Return_t, -clip, +clip)
          - w2 * DrawdownPenalty
          - w3 * TurnoverPenalty
          - w_sortino * DownsideDeviationPenalty
          - w_conc * ConcentrationPenalty

Design intent (why each term exists):

  * ``clip(w1 * Return_t)`` -- the ONLY positive term, hard-clipped so the policy
    cannot chase unbounded upside. This is the core anti-greed constraint: it
    removes the incentive to discover fragile, hyper-aggressive "exploit-seeking"
    strategies that print one enormous return then blow up.
  * ``DrawdownPenalty`` -- convex penalty on peak-to-trough equity loss.
  * ``TurnoverPenalty`` -- taxes excessive trading (churn), which both bleeds fees
    and is a classic reward-hacking signature (over-trading to farm noise).
  * ``DownsideDeviationPenalty`` -- a Sortino-style approximation: only *downside*
    volatility is punished, so the policy is free to be volatile to the upside.
  * ``ConcentrationPenalty`` -- discourages betting the book on a single
    concentrated, correlated exposure.

Auxiliary-task scaling: data generated in SIMULATION_MODE is a pre-training
auxiliary task only. Simulated gradients are scaled by ``alpha_sim <= 0.15`` via
:func:`simulated_gradient_scale` / :func:`apply_auxiliary_weight` so the sandbox
teaches structural contingencies without mutating production weights directly.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

# Hard cap on the auxiliary (simulation) gradient weight. Simulated RL must never
# dominate the real-world objective -- it is strictly a pre-training signal.
SIM_WEIGHT_CEILING: float = 0.15


@dataclass(slots=True, frozen=True)
class RewardConfig:
    """Weights and safety bounds for the shaped reward.

    ``w_return``/``w_drawdown``/``w_turnover`` are the ``w1``/``w2``/``w3`` of the
    documented formula. The remaining weights add Sortino + concentration control.
    """

    w_return: float = 1.0            # w1: weight on the (clipped) return term
    w_drawdown: float = 1.0          # w2: weight on the drawdown penalty
    w_turnover: float = 0.50         # w3: weight on the turnover / churn penalty
    w_sortino: float = 0.35          # weight on downside-deviation penalty
    w_concentration: float = 0.25    # weight on concentrated-exposure penalty

    return_clip: float = 1.0         # anti-greed hard clip on the return term
    reward_floor: float = -2.0       # final reward is bounded below (stable PPO)
    reward_ceiling: float = 1.0      # final reward is bounded above (anti-greed)

    target_return: float = 0.0       # MAR for the Sortino downside computation
    turnover_free: float = 0.10      # turnover below this is not penalised
    turnover_scale: float = 1.0      # slope of the turnover penalty above free band

    # Auxiliary (simulation) task weight. Clamped to SIM_WEIGHT_CEILING on use.
    sim_weight: float = SIM_WEIGHT_CEILING

    def __post_init__(self) -> None:  # keep weights sane / non-negative
        object.__setattr__(self, "w_return", max(0.0, self.w_return))
        object.__setattr__(self, "w_drawdown", max(0.0, self.w_drawdown))
        object.__setattr__(self, "w_turnover", max(0.0, self.w_turnover))
        object.__setattr__(self, "w_sortino", max(0.0, self.w_sortino))
        object.__setattr__(self, "w_concentration", max(0.0, self.w_concentration))
        object.__setattr__(self, "return_clip", max(1e-6, self.return_clip))


@dataclass(slots=True, frozen=True)
class RewardBreakdown:
    """Component-wise decomposition of a single step reward (for XAI / logging)."""

    reward: float
    return_term: float
    drawdown_penalty: float
    turnover_penalty: float
    sortino_penalty: float
    concentration_penalty: float
    clipped: bool

    def as_dict(self) -> dict[str, float | bool]:
        return {
            "reward": self.reward,
            "return_term": self.return_term,
            "drawdown_penalty": self.drawdown_penalty,
            "turnover_penalty": self.turnover_penalty,
            "sortino_penalty": self.sortino_penalty,
            "concentration_penalty": self.concentration_penalty,
            "clipped": self.clipped,
        }


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def downside_deviation(returns: list[float], target: float = 0.0) -> float:
    """Root-mean-square of shortfalls below ``target`` (Sortino denominator).

    Unlike standard deviation, only observations *below* the minimum acceptable
    return contribute, so upside volatility is never punished.
    """
    shortfalls = [min(0.0, r - target) for r in returns]
    if len(shortfalls) < 2:
        return abs(shortfalls[0]) if shortfalls else 0.0
    mean_sq = sum(s * s for s in shortfalls) / len(shortfalls)
    return mean_sq ** 0.5


def sortino_ratio(
    step_return: float, equity_returns: list[float], target: float = 0.0
) -> float:
    """Dynamic Sortino-ratio approximation: excess return per unit of downside risk."""
    dd = downside_deviation(equity_returns, target)
    if dd <= 1e-9:
        return 0.0
    return (step_return - target) / dd


def turnover_penalty(
    turnover: float, free_band: float = 0.10, scale: float = 1.0
) -> float:
    """Convex penalty on trading churn beyond a small free band.

    ``turnover`` is the fraction of book turned over this step (|Δposition|,
    normalised to ``[0, 1+]``). Below ``free_band`` there is no penalty; above it
    the penalty grows quadratically so hyper-active over-trading is punished hard.
    """
    excess = max(0.0, turnover - free_band)
    return scale * excess * excess


def concentration_penalty(position_concentration: float) -> float:
    """Convex penalty on concentrated / correlated single-name exposure.

    ``position_concentration`` in ``[0, 1]`` (e.g. a Herfindahl-style index; 1.0
    means the whole book is one correlated bet).
    """
    c = _clip(position_concentration, 0.0, 1.0)
    return c * c


def multi_objective_reward(
    step_return: float,
    max_drawdown: float,
    equity_returns: list[float],
    turnover: float = 0.0,
    position_concentration: float = 0.0,
    config: RewardConfig | None = None,
) -> float:
    """Compute the shaped, anti-greed reward for a single PPO step.

    Parameters
    ----------
    step_return
        Per-step account return (fractional, e.g. 0.004 == +0.4%).
    max_drawdown
        Current peak-to-trough equity drawdown (fractional, >= 0).
    equity_returns
        Recent window of per-step returns, used for the Sortino downside term.
    turnover
        Fraction of the book turned over this step (drives the churn penalty).
    position_concentration
        Herfindahl-style concentration of exposure in ``[0, 1]``.
    """
    return breakdown_reward(
        step_return,
        max_drawdown,
        equity_returns,
        turnover=turnover,
        position_concentration=position_concentration,
        config=config,
    ).reward


def breakdown_reward(
    step_return: float,
    max_drawdown: float,
    equity_returns: list[float],
    turnover: float = 0.0,
    position_concentration: float = 0.0,
    config: RewardConfig | None = None,
) -> RewardBreakdown:
    """Full component decomposition of :func:`multi_objective_reward`."""
    cfg = config or RewardConfig()

    # 1) Anti-greed return term: clip so the policy cannot chase unbounded upside.
    raw_return = cfg.w_return * step_return
    return_term = _clip(raw_return, -cfg.return_clip, cfg.return_clip)
    return_clipped = raw_return != return_term

    # 2) Drawdown penalty (convex: a 2x deeper drawdown hurts ~4x as much).
    dd = max(0.0, max_drawdown)
    dd_penalty = cfg.w_drawdown * dd * (1.0 + dd)

    # 3) Turnover / churn penalty (anti reward-hacking via over-trading).
    to_penalty = cfg.w_turnover * turnover_penalty(
        turnover, cfg.turnover_free, cfg.turnover_scale
    )

    # 4) Sortino downside-deviation penalty (only downside vol is punished).
    dsd = downside_deviation(equity_returns, cfg.target_return)
    sortino_pen = cfg.w_sortino * dsd

    # 5) Concentration penalty (discourage all-in correlated bets).
    conc_pen = cfg.w_concentration * concentration_penalty(position_concentration)

    reward = return_term - dd_penalty - to_penalty - sortino_pen - conc_pen
    bounded = _clip(reward, cfg.reward_floor, cfg.reward_ceiling)

    return RewardBreakdown(
        reward=bounded,
        return_term=return_term,
        drawdown_penalty=dd_penalty,
        turnover_penalty=to_penalty,
        sortino_penalty=sortino_pen,
        concentration_penalty=conc_pen,
        clipped=return_clipped or bounded != reward,
    )


# ===========================================================================
#  Auxiliary (SIMULATION_MODE) gradient scaling  --  alpha_sim <= 0.15
# ===========================================================================
def simulated_gradient_scale(config: RewardConfig | None = None) -> float:
    """Return the auxiliary-task weight ``alpha_sim``, hard-capped at 0.15.

    Simulated RL is a pre-training auxiliary signal ONLY; its gradients must be
    scaled down so they teach structural contingencies without directly mutating
    the production neural-network weights.
    """
    cfg = config or RewardConfig()
    return _clip(cfg.sim_weight, 0.0, SIM_WEIGHT_CEILING)


def apply_auxiliary_weight(
    reward: float,
    *,
    is_simulation: bool,
    config: RewardConfig | None = None,
) -> float:
    """Scale a reward by ``alpha_sim`` when it originates from SIMULATION_MODE.

    In REALITY_MODE the reward passes through untouched (full weight); in
    SIMULATION_MODE it is multiplied by ``alpha_sim <= 0.15``.
    """
    if not is_simulation:
        return reward
    return simulated_gradient_scale(config) * reward
