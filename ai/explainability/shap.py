"""ECONITH :: ai.explainability.shap

Feature-attribution export (master plan, Phase 5, Step 1 + Research 7).

The full system extracts sub-millisecond SHAP values from the trained policy.
For the Phase 2 stub we aggregate the per-feature ``contributions`` each agent
already reports (weighted by the regime allocation) into a normalised
attribution map, then serialise it to transparent JSON for the dashboard /
Telegram. The interface matches what a real SHAP explainer will emit, so the
downstream JSON contract never changes.
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
        "action": decision_action,
        "direction": round(direction, 4),
        "attribution": [{"feature": k, "importance": v} for k, v in ranked],
    }
