"""ECONITH :: ai.explainability.attribution

Feature-attribution export (master plan, Phase 5).

Phase 2 aggregates the per-feature ``contributions`` each agent already reports
(weighted by the regime allocation) into a normalised attribution map, then
serialises it for the dashboard / Telegram. This is **weighted feature
attribution**, not SHAP. A real SHAP explainer can later implement the same
``build_attribution`` / ``attribution_to_json`` contract.
"""
from __future__ import annotations

from ai.agents.base import AgentSignal
from ai.regime.switcher import Allocation


def build_attribution(
    signals: list[AgentSignal],
    allocation: Allocation,
) -> dict[str, float]:
    """Aggregate weighted per-feature contributions into one attribution map."""
    attribution: dict[str, float] = {}
    for sig in signals:
        w = allocation.weight_for(sig.agent)
        for feature, value in sig.contributions.items():
            attribution[feature] = attribution.get(feature, 0.0) + w * value

    # Normalise to fractions of total absolute attribution (importance share).
    total = sum(abs(v) for v in attribution.values()) or 1.0
    return {k: round(v / total, 4) for k, v in attribution.items()}


def attribution_to_json(
    decision_action: str,
    direction: float,
    attribution: dict[str, float],
) -> dict:
    """Wrap an attribution map in the dashboard-facing JSON envelope."""
    ranked = sorted(attribution.items(), key=lambda kv: abs(kv[1]), reverse=True)
    return {
        "method": "weighted_feature_attribution",
        "action": decision_action,
        "direction": round(direction, 4),
        "top_features": [{"feature": k, "weight": v} for k, v in ranked[:8]],
        "attribution": attribution,
    }
