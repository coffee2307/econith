"""ECONITH :: ai.ensemble.decision_fusion

Fuses per-agent signals into one portfolio decision (master plan, Phase 2 +
Research 3). Each agent's directional opinion is weighted by (a) the regime
switcher's capital allocation and (b) the agent's own confidence, then summed.

This resolves inter-agent conflict (Research Q3.3) by net-weighting opposing
opinions rather than letting a single agent dominate.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ai.agents.base import AgentSignal
from ai.regime.switcher import Allocation


@dataclass(slots=True, frozen=True)
class FusedDecision:
    direction: float            # net signal in [-1, +1]
    action: str                 # LONG | SHORT | FLAT
    confidence: float           # 0..1
    regime: str
    weights: dict[str, float] = field(default_factory=dict)
    per_agent: dict[str, float] = field(default_factory=dict)


def _action(direction: float, deadband: float = 0.05) -> str:
    if direction > deadband:
        return "LONG"
    if direction < -deadband:
        return "SHORT"
    return "FLAT"


def fuse_signals(
    signals: list[AgentSignal],
    allocation: Allocation,
    deadband: float = 0.05,
) -> FusedDecision:
    net = 0.0
    conf_acc = 0.0
    weight_acc = 0.0
    per_agent: dict[str, float] = {}

    for sig in signals:
        w = allocation.weight_for(sig.agent)
        contribution = w * sig.confidence * sig.direction
        per_agent[sig.agent] = round(contribution, 4)
        net += contribution
        conf_acc += w * sig.confidence
        weight_acc += w

    direction = max(-1.0, min(1.0, net))
    confidence = round(conf_acc / weight_acc, 4) if weight_acc > 0 else 0.0
    return FusedDecision(
        direction=round(direction, 4),
        action=_action(direction, deadband),
        confidence=confidence,
        regime=allocation.regime,
        weights=allocation.weights,
        per_agent=per_agent,
    )
