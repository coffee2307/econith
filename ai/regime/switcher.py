"""ECONITH :: ai.regime.switcher

Dynamic Regime Switcher (master plan, Phase 2, Step 2).

Maps the current regime probability distribution to capital-allocation weights
across the three agents. Rather than a hard winner-take-all switch (which would
whipsaw on regime boundaries), it blends a per-regime weight profile by the
regime probabilities -- a smooth handover that addresses Research Q3.1
(handling open positions during a regime change).
"""
from __future__ import annotations

from dataclasses import dataclass

from ai.regime.classifier import RegimeState

# Per-regime preferred agent weighting (rows sum to ~1.0).
#                       trend  mean_rev  scalper
_PROFILES: dict[str, dict[str, float]] = {
    "TRENDING":       {"trend": 0.70, "mean_reversion": 0.05, "scalper": 0.25},
    "MEAN_REVERTING": {"trend": 0.10, "mean_reversion": 0.70, "scalper": 0.20},
    "VOLATILE":       {"trend": 0.20, "mean_reversion": 0.20, "scalper": 0.60},
    "CALM":           {"trend": 0.34, "mean_reversion": 0.33, "scalper": 0.33},
}

AGENTS = ("trend", "mean_reversion", "scalper")


@dataclass(slots=True, frozen=True)
class Allocation:
    weights: dict[str, float]
    regime: str

    def weight_for(self, agent: str) -> float:
        return self.weights.get(agent, 0.0)


class RegimeSwitcher:
    """Blends agent allocation weights by the regime distribution."""

    def allocate(self, regime: RegimeState) -> Allocation:
        probs = regime.probabilities or {regime.label: 1.0}
        blended = {a: 0.0 for a in AGENTS}
        for label, p in probs.items():
            profile = _PROFILES.get(label, _PROFILES["CALM"])
            for agent in AGENTS:
                blended[agent] += p * profile[agent]

        total = sum(blended.values()) or 1.0
        weights = {a: round(w / total, 4) for a, w in blended.items()}
        return Allocation(weights=weights, regime=regime.label)
