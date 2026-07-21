"""ECONITH :: econith.world.sovereign.agent_cluster

Scalable **agent-cluster** substrate — the batched ECS blueprint that lets
ECONITH World represent ~1,000,000 agents across 150 nations (50 hubs + 100
proxies) WITHOUT bricking a
local box (Task 2).

Why batched arrays instead of a million Python objects
------------------------------------------------------
A million per-agent Python objects would cost multiple GB and step in seconds.
Instead we use a **Structure-of-Arrays / clustered Entity-Component-System**:
agents are grouped into homogeneous *clusters* (one cluster = one nation × one
class), and every cluster is a fixed-width NumPy row. A "cluster" is a
statistical population, not an individual; behaviour is applied per-cluster with
vectorised math, so the whole world steps in a single handful of array ops.

    class GOVERNMENT   -> fiscal / policy pressure  (few, heavy agents)
    class CORPORATE    -> supply-chain / capital flight (many mid agents)
    class CONSUMER     -> demand / unrest / sentiment (the population mass)

Each cluster carries the components the butterfly dynamics need:

    population   headcount the cluster stands in for (Σ ≈ 1e6)
    activity     0..1 economic activity / confidence
    stress       0..1 distress (drives crisis + capital flight)
    mobility     0..1 propensity to relocate / reallocate
    output       relative economic output index

Compute guardrail
-----------------
The engine NEVER schedules its own loop; a host calls :meth:`step` once per tick
and MUST skip the call when the operator's *Enable World Simulation* switch is
OFF (see ``core.system_controller``). :meth:`capacity` reports the represented
head-count so the dashboard can honestly print "≈1M agents" while stepping only
``n_clusters`` rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

from econith.world.sovereign.topology import ALL_CODES, HUB_CODES

__all__ = [
    "AgentClass",
    "ClusterField",
    "AgentClusterConfig",
    "AgentClusterEngine",
    "ClusterTelemetry",
]


# --- agent taxonomy ----------------------------------------------------------
class AgentClass:
    GOVERNMENT: Final[int] = 0
    CORPORATE: Final[int] = 1
    CONSUMER: Final[int] = 2
    N_CLASSES: Final[int] = 3


# --- component columns (Structure-of-Arrays layout) --------------------------
class ClusterField:
    POPULATION: Final[int] = 0   # represented head-count
    ACTIVITY: Final[int] = 1     # 0..1 economic activity / confidence
    STRESS: Final[int] = 2       # 0..1 distress
    MOBILITY: Final[int] = 3     # 0..1 relocation / reallocation propensity
    OUTPUT: Final[int] = 4       # relative output index
    N_FIELDS: Final[int] = 5


# Per-class share of a nation's represented population and behavioural priors.
# (population_share, base_activity, base_mobility)
_CLASS_PRIORS: dict[int, tuple[float, float, float]] = {
    AgentClass.GOVERNMENT: (0.001, 0.70, 0.05),   # tiny headcount, low mobility
    AgentClass.CORPORATE: (0.049, 0.65, 0.55),    # mobile capital
    AgentClass.CONSUMER: (0.950, 0.60, 0.15),     # the population mass
}


@dataclass(slots=True)
class AgentClusterConfig:
    """Sizing + physics knobs for the clustered world."""

    #: total represented agents spread across all nation×class clusters
    target_agents: int = 1_000_000
    #: hubs get the lion's share of headcount; proxies a thin tail
    hub_population_weight: float = 4.0
    #: crisis fires when a cluster's stress exceeds this
    crisis_stress: float = 0.75
    #: mean-reversion pull of activity back toward its class baseline
    recovery_rate: float = 0.03
    #: how hard external shock raises stress
    shock_gain: float = 0.6
    #: contagion: fraction of a stressed cluster's stress that spills to peers
    contagion: float = 0.15


@dataclass(slots=True)
class ClusterTelemetry:
    """One-tick summary for the dashboard / stress harness."""

    tick: int
    n_clusters: int
    represented_agents: int
    step_ms: float
    mean_activity: float
    mean_stress: float
    crisis_clusters: int
    capital_flight_index: float
    mean_reward: float
    action_mix: dict[str, float]


class AgentClusterEngine:
    """Batched ECS stepper for the clustered macro-agent population."""

    def __init__(self, config: AgentClusterConfig | None = None) -> None:
        self._cfg = config or AgentClusterConfig()
        self._codes: tuple[str, ...] = ALL_CODES
        self._n_nodes = len(self._codes)
        self._hub_set = frozenset(HUB_CODES)
        self._n_classes = AgentClass.N_CLASSES
        self._n_clusters = self._n_nodes * self._n_classes
        # State tensor: (n_nodes, n_classes, n_fields) — Structure-of-Arrays.
        self._state = np.zeros(
            (self._n_nodes, self._n_classes, ClusterField.N_FIELDS), dtype=np.float64
        )
        # Contextual-bandit policy: preserve / invest / adapt.  Q-values are
        # learned independently by every nation×class cluster from utility
        # changes produced by its own and neighbouring clusters' actions.
        self._n_actions = 3
        self._q = np.zeros(
            (self._n_nodes, self._n_classes, self._n_actions), dtype=np.float64
        )
        self._last_actions = np.zeros(
            (self._n_nodes, self._n_classes), dtype=np.int8
        )
        self._last_reward = np.zeros(
            (self._n_nodes, self._n_classes), dtype=np.float64
        )
        self._rng = np.random.default_rng(20_260_717)
        # Persistent heterogeneous preferences prevent every cluster from
        # taking the same action under identical aggregate conditions.
        self._policy_traits = self._rng.normal(
            0.0, 0.16, size=(self._n_nodes, self._n_classes, self._n_actions)
        )
        self._tick = 0
        self._seed_population()

    # -- construction ---------------------------------------------------------
    def _seed_population(self) -> None:
        cfg = self._cfg
        # Node headcount weights: hubs weighted heavier than proxies.
        weights = np.array(
            [cfg.hub_population_weight if c in self._hub_set else 1.0 for c in self._codes],
            dtype=np.float64,
        )
        weights /= weights.sum()
        node_pop = weights * float(cfg.target_agents)

        for cls_id, (pop_share, activity, mobility) in _CLASS_PRIORS.items():
            self._state[:, cls_id, ClusterField.POPULATION] = node_pop * pop_share
            self._state[:, cls_id, ClusterField.ACTIVITY] = activity
            self._state[:, cls_id, ClusterField.MOBILITY] = mobility
            self._state[:, cls_id, ClusterField.STRESS] = 0.05
            self._state[:, cls_id, ClusterField.OUTPUT] = 1.0

    # -- introspection --------------------------------------------------------
    @property
    def n_clusters(self) -> int:
        return self._n_clusters

    def capacity(self) -> int:
        """Represented head-count (Σ populations) — the honest '≈1M agents'."""
        return int(round(float(self._state[:, :, ClusterField.POPULATION].sum())))

    def node_index(self, code: str) -> int:
        return self._codes.index(code.upper())

    def country_signals(self, code: str) -> dict[str, float | str]:
        """Policy read-model consumed by WorldKernel's classic country state."""
        idx = self.node_index(code)
        row = self._state[idx]
        action_names = ("preserve", "invest", "adapt")
        return {
            "government_stress": float(row[AgentClass.GOVERNMENT, ClusterField.STRESS]),
            "corporate_activity": float(row[AgentClass.CORPORATE, ClusterField.ACTIVITY]),
            "corporate_output": float(row[AgentClass.CORPORATE, ClusterField.OUTPUT]),
            "consumer_activity": float(row[AgentClass.CONSUMER, ClusterField.ACTIVITY]),
            "consumer_stress": float(row[AgentClass.CONSUMER, ClusterField.STRESS]),
            "government_action": action_names[
                int(self._last_actions[idx, AgentClass.GOVERNMENT])
            ],
            "corporate_action": action_names[
                int(self._last_actions[idx, AgentClass.CORPORATE])
            ],
            "consumer_action": action_names[
                int(self._last_actions[idx, AgentClass.CONSUMER])
            ],
        }

    # -- the batched tick -----------------------------------------------------
    def step(
        self,
        *,
        external_stress: dict[str, float] | None = None,
        global_stress: float = 0.0,
        scale: float = 1.0,
    ) -> ClusterTelemetry:
        """Advance every cluster one tick with pure vectorised NumPy.

        ``external_stress`` maps nation code -> [0,1] shock intensity (e.g. a
        tariff / crisis landing on that nation); ``global_stress`` is a
        market-wide floor. The host is responsible for NOT calling this when the
        compute guardrail is OFF.
        """
        from time import perf_counter

        t0 = perf_counter()
        cfg = self._cfg
        st = self._state

        # 1) External + global shock -> raise stress (broadcast per node).
        shock = np.full(self._n_nodes, float(global_stress), dtype=np.float64)
        if external_stress:
            for code, val in external_stress.items():
                c = code.upper()
                if c in self._codes:
                    shock[self.node_index(c)] = max(shock[self.node_index(c)], float(val))
        shock2d = shock[:, None]  # (n_nodes, 1) broadcast across classes

        stress = st[:, :, ClusterField.STRESS]
        stress += scale * cfg.shock_gain * shock2d * (1.0 - stress)

        # 2) Corporate mobility amplifies stress transmission (capital flight).
        mobility = st[:, :, ClusterField.MOBILITY]
        stress += scale * 0.05 * mobility * shock2d
        np.clip(stress, 0.0, 1.0, out=stress)

        # 3) Contagion: each node's mean stress spills to every class next tick.
        node_mean_stress = stress.mean(axis=1, keepdims=True)  # (n_nodes, 1)
        stress += scale * cfg.contagion * (node_mean_stress - stress) * (node_mean_stress > stress)
        np.clip(stress, 0.0, 1.0, out=stress)

        # 4) Every cluster chooses among bounded economic primitives using a
        # contextual policy plus its learned Q-values.  This is one vectorized
        # decision per represented government/corporate/consumer population,
        # not a scripted scenario lookup.
        utility_before = self._utility()
        actions = self._select_actions(stress)
        self._apply_actions(actions, scale)

        # 5) Activity: stress depresses it; recovery pulls it back to baseline.
        activity = st[:, :, ClusterField.ACTIVITY]
        baseline = np.array(
            [_CLASS_PRIORS[c][1] for c in range(self._n_classes)], dtype=np.float64
        )[None, :]
        activity += scale * (
            cfg.recovery_rate * (baseline - activity) - 0.20 * stress * activity
        )
        np.clip(activity, 0.0, 1.0, out=activity)

        # 6) Output tracks activity (soft, bounded).
        out = st[:, :, ClusterField.OUTPUT]
        out += scale * 0.10 * (activity - out)
        np.clip(out, 0.0, 3.0, out=out)

        st[:, :, ClusterField.STRESS] = stress
        st[:, :, ClusterField.ACTIVITY] = activity
        st[:, :, ClusterField.OUTPUT] = out

        # Immediate online learning. Reward is objective improvement after the
        # chosen action and cross-class spillovers; no labelled scenario is
        # required.  Bad actions lose probability on subsequent ticks.
        reward = np.clip(self._utility() - utility_before, -1.0, 1.0)
        rows = np.arange(self._n_nodes)[:, None]
        classes = np.arange(self._n_classes)[None, :]
        chosen_q = self._q[rows, classes, actions]
        chosen_q += 0.12 * (reward - chosen_q)
        self._q[rows, classes, actions] = np.clip(chosen_q, -1.5, 1.5)
        self._last_actions = actions.astype(np.int8)
        self._last_reward = reward

        self._tick += 1
        step_ms = (perf_counter() - t0) * 1_000.0

        # Capital-flight index: population-weighted corporate stress×mobility.
        corp = st[:, AgentClass.CORPORATE, :]
        corp_pop = corp[:, ClusterField.POPULATION]
        cf = float(
            (corp[:, ClusterField.STRESS] * corp[:, ClusterField.MOBILITY] * corp_pop).sum()
            / max(1.0, corp_pop.sum())
        )
        crisis = int((stress >= cfg.crisis_stress).sum())
        action_mix = {
            "preserve": float(np.mean(actions == 0)),
            "invest": float(np.mean(actions == 1)),
            "adapt": float(np.mean(actions == 2)),
        }

        return ClusterTelemetry(
            tick=self._tick,
            n_clusters=self._n_clusters,
            represented_agents=self.capacity(),
            step_ms=step_ms,
            mean_activity=float(activity.mean()),
            mean_stress=float(stress.mean()),
            crisis_clusters=crisis,
            capital_flight_index=cf,
            mean_reward=float(reward.mean()),
            action_mix=action_mix,
        )

    def _utility(self) -> np.ndarray:
        """Per-cluster objective; each class values a different outcome mix."""
        activity = self._state[:, :, ClusterField.ACTIVITY]
        stress = self._state[:, :, ClusterField.STRESS]
        output = self._state[:, :, ClusterField.OUTPUT]
        weights = np.array(
            [
                (0.40, -0.75, 0.40),  # government: stability and output
                (0.30, -0.55, 0.55),  # corporate: output with bounded risk
                (0.50, -0.85, 0.20),  # consumers: welfare and security
            ],
            dtype=np.float64,
        )
        return (
            activity * weights[None, :, 0]
            + stress * weights[None, :, 1]
            + output * weights[None, :, 2]
        )

    def _select_actions(self, stress: np.ndarray) -> np.ndarray:
        activity = self._state[:, :, ClusterField.ACTIVITY]
        output = self._state[:, :, ClusterField.OUTPUT]
        mobility = self._state[:, :, ClusterField.MOBILITY]
        logits = np.empty_like(self._q)
        logits[:, :, 0] = 0.18 + 0.10 * activity
        logits[:, :, 1] = 0.15 + 0.75 * (1.0 - stress) + 0.25 * activity
        logits[:, :, 2] = -0.10 + 1.10 * stress + 0.20 * mobility - 0.10 * output
        logits += self._q + self._policy_traits
        # Gumbel-max samples a softmax policy without Python loops. Exploration
        # lets clusters discover better actions as the simulated regime changes.
        noise = self._rng.gumbel(0.0, 0.30, size=logits.shape)
        return np.argmax(logits + noise, axis=2)

    def _apply_actions(self, actions: np.ndarray, scale: float) -> None:
        st = self._state
        stress = st[:, :, ClusterField.STRESS]
        activity = st[:, :, ClusterField.ACTIVITY]
        output = st[:, :, ClusterField.OUTPUT]
        mobility = st[:, :, ClusterField.MOBILITY]
        invest = actions == 1
        adapt = actions == 2

        activity += scale * 0.018 * invest * (1.0 - stress)
        output += scale * 0.014 * invest * (1.0 - stress)

        # The same abstract "adapt" primitive has class-specific consequences.
        # Government stabilisation cushions all domestic classes.
        govt_adapt = adapt[:, AgentClass.GOVERNMENT][:, None]
        stress -= scale * 0.025 * govt_adapt
        # Corporate reallocation protects firms but temporarily hurts workers
        # and tax capacity; corporate investment has the opposite spillover.
        corp_adapt = adapt[:, AgentClass.CORPORATE]
        stress[:, AgentClass.CORPORATE] -= scale * 0.015 * corp_adapt
        mobility[:, AgentClass.CORPORATE] += scale * 0.012 * corp_adapt
        activity[:, AgentClass.CONSUMER] -= scale * 0.010 * corp_adapt
        corp_invest = invest[:, AgentClass.CORPORATE]
        activity[:, AgentClass.CONSUMER] += scale * 0.008 * corp_invest
        # Household precautionary saving lowers current corporate demand while
        # reducing consumer stress exposure.
        consumer_adapt = adapt[:, AgentClass.CONSUMER]
        stress[:, AgentClass.CONSUMER] -= scale * 0.010 * consumer_adapt
        output[:, AgentClass.CORPORATE] -= scale * 0.006 * consumer_adapt

        np.clip(stress, 0.0, 1.0, out=stress)
        np.clip(activity, 0.0, 1.0, out=activity)
        np.clip(output, 0.0, 3.0, out=output)
        np.clip(mobility, 0.0, 1.0, out=mobility)

    # -- read model -----------------------------------------------------------
    def snapshot(self, top_n: int = 10) -> dict[str, object]:
        """Compact dashboard view: aggregates + the most-stressed nations."""
        stress = self._state[:, :, ClusterField.STRESS]
        node_stress = stress.mean(axis=1)
        order = np.argsort(node_stress)[::-1][:top_n]
        hotspots = [
            {"code": self._codes[i], "stress": round(float(node_stress[i]), 4)}
            for i in order
        ]
        return {
            "tick": self._tick,
            "n_nodes": self._n_nodes,
            "n_classes": self._n_classes,
            "n_clusters": self._n_clusters,
            "represented_agents": self.capacity(),
            "mean_stress": round(float(stress.mean()), 4),
            "mean_activity": round(float(self._state[:, :, ClusterField.ACTIVITY].mean()), 4),
            "mean_reward": round(float(self._last_reward.mean()), 6),
            "action_mix": {
                "preserve": round(float(np.mean(self._last_actions == 0)), 4),
                "invest": round(float(np.mean(self._last_actions == 1)), 4),
                "adapt": round(float(np.mean(self._last_actions == 2)), 4),
            },
            "hotspots": hotspots,
        }
