"""ECONITH :: ai.agents.scalper.agent

Micro-scalper agent (master plan, Phase 2, Step 1, Agent 3).

Operates purely on second-scale order-flow: OBI and Volume Delta. It chases the
very short-term pressure, so it follows imbalance rather than fading it, with
high confidence only when OBI and Volume Delta agree.
"""
from __future__ import annotations

from typing import Any

from ai.agents.base import AgentSignal, BaseAgent


class ScalperAgent(BaseAgent):
    name = "scalper"
    feature_keys = ("obi", "volume_delta", "buy_volume", "sell_volume")

    def act(self, features: dict[str, Any]) -> AgentSignal:
        obi = self._f(features, "obi")
        buy = self._f(features, "buy_volume")
        sell = self._f(features, "sell_volume")
        total = buy + sell
        flow = (buy - sell) / total if total > 0 else 0.0

        direction = self.clamp(0.7 * obi + 0.6 * flow)
        # Agreement between book imbalance and trade flow boosts confidence.
        agreement = 1.0 if (obi >= 0) == (flow >= 0) else 0.4
        confidence = min(1.0, abs(direction) * agreement)
        return AgentSignal(
            agent=self.name,
            direction=direction,
            confidence=confidence,
            rationale="short-horizon OBI + trade-flow chase",
            contributions={"obi": 0.7 * obi, "trade_flow": 0.6 * flow},
        )
