"""ECONITH :: ai.agents.trend.agent

Trend-following agent (master plan, Phase 2, Step 1, Agent 1).

Focuses on long-horizon trend features (EMA / MACD / ADX in the full system).
In this Phase 2 stub it reads order-book imbalance momentum plus open-interest
change as a directional proxy until the trained PPO policy replaces ``act()``.
"""
from __future__ import annotations

from typing import Any

from ai.agents.base import AgentSignal, BaseAgent


class TrendAgent(BaseAgent):
    name = "trend"
    feature_keys = ("obi", "oi_change_pct", "volume_delta")

    def act(self, features: dict[str, Any]) -> AgentSignal:
        obi = self._f(features, "obi")
        oi_chg = self._f(features, "oi_change_pct")
        vd = self._f(features, "volume_delta")

        # Rising OI + positive imbalance + positive delta == trend confirmation.
        raw = 0.6 * obi + 80.0 * oi_chg + 0.05 * _sign(vd)
        direction = self.clamp(raw)
        confidence = min(1.0, abs(direction))
        return AgentSignal(
            agent=self.name,
            direction=direction,
            confidence=confidence,
            rationale="OBI momentum + OI expansion",
            contributions={
                "obi": 0.6 * obi,
                "oi_change_pct": 80.0 * oi_chg,
                "volume_delta": 0.05 * _sign(vd),
            },
        )


def _sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)
