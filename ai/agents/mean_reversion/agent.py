"""ECONITH :: ai.agents.mean_reversion.agent

Mean-reversion agent (master plan, Phase 2, Step 1, Agent 2).

Targets exhaustion in range-bound markets (RSI / Bollinger / Stochastic in the
full system). This stub fades order-book imbalance extremes: a strongly
one-sided book is treated as over-extension and faded toward the mean.
"""
from __future__ import annotations

from typing import Any

from ai.agents.base import AgentSignal, BaseAgent


class MeanReversionAgent(BaseAgent):
    name = "mean_reversion"
    feature_keys = ("obi", "volume_delta")

    def act(self, features: dict[str, Any]) -> AgentSignal:
        obi = self._f(features, "obi")
        vd = self._f(features, "volume_delta")

        # Fade extremes: invert the imbalance, scaled by how stretched it is.
        stretch = abs(obi)
        direction = self.clamp(-obi * (0.5 + stretch))
        # Confidence rises near extremes, falls near a balanced book.
        confidence = min(1.0, stretch * 1.5)
        return AgentSignal(
            agent=self.name,
            direction=direction,
            confidence=confidence,
            rationale="fade OBI extreme toward mean",
            contributions={"obi": -obi * (0.5 + stretch), "volume_delta": 0.0 * vd},
        )
