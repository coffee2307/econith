"""ECONITH :: econith.world.core.dialogue_schema

Structured causal-dialogue types. LLMs may author *stance text* and pick
*action enums*; every numeric figure that reaches the UI or physics must be a
:class:`GroundedMetric` produced by the simulation, never invented prose digits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "ACTION_ENUMS",
    "ACTION_TO_DELTAS",
    "POLICY_ROLES",
    "GroundedMetric",
    "DialogueDecision",
    "DialogueUtterance",
    "DialogueTurnBundle",
    "AgentPersona",
]

# Allowlisted continuous-policy moves. The LLM picks an enum; a deterministic
# mapper converts it into clamped macro deltas (see ACTION_TO_DELTAS).
ACTION_ENUMS: Final[frozenset[str]] = frozenset({
    "hold",
    "ease",
    "tighten",
    "qe_pulse",
    "tariff_defend",
    "tariff_ease",
    "tax_cut",
    "tax_hike",
    "bargain_accept",
    "bargain_resist",
})

# Roles allowed to emit macro policy deltas (rate / tax / tariff / QE).
POLICY_ROLES: Final[frozenset[str]] = frozenset({
    "Central Bank",
    "Government",
    "Ngân hàng trung ương",
    "Chính phủ",
})

# Deterministic enum → per-tick delta recipe (before GovernorDirective clamps).
ACTION_TO_DELTAS: Final[dict[str, dict[str, float]]] = {
    "hold": {},
    "ease": {"interest_rate_delta": -0.005, "money_supply_delta": 0.01, "stance": -0.4},
    "tighten": {"interest_rate_delta": 0.005, "money_supply_delta": -0.01, "stance": 0.5},
    "qe_pulse": {"money_supply_delta": 0.04, "interest_rate_delta": -0.002, "stance": -0.6},
    "tariff_defend": {"tariff_delta": 0.03, "stance": 0.3},
    "tariff_ease": {"tariff_delta": -0.02, "stance": -0.2},
    "tax_cut": {"tax_delta": -0.01, "stance": -0.3},
    "tax_hike": {"tax_delta": 0.01, "stance": 0.3},
    "bargain_accept": {"stance": -0.1},
    "bargain_resist": {"stance": 0.2},
}

# Non-policy actors may only pick negotiation / hold moves.
_NON_POLICY_ACTIONS: Final[frozenset[str]] = frozenset({
    "hold", "bargain_accept", "bargain_resist",
})


@dataclass(slots=True)
class GroundedMetric:
    """A number that originated in physics / negotiation / feedback."""

    name: str
    value: float
    unit: str = ""
    source_tick: int = 0
    event_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": float(self.value),
            "unit": self.unit,
            "source_tick": int(self.source_tick),
            "event_id": self.event_id,
        }


@dataclass(slots=True)
class AgentPersona:
    """A cast member for one deliberation episode — built from live events."""

    agent_id: str
    role: str
    country: str
    objective: str
    can_set_policy: bool = False
    allowed_actions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "country": self.country,
            "objective": self.objective,
            "can_set_policy": self.can_set_policy,
            "allowed_actions": list(self.allowed_actions),
        }


@dataclass(slots=True)
class DialogueDecision:
    """Validated action a meso/macro agent wants to take."""

    agent_id: str
    country: str
    action_id: str
    params: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    grounding: list[str] = field(default_factory=list)
    confidence: float = 0.5
    responds_to: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "country": self.country,
            "action_id": self.action_id,
            "params": {k: float(v) for k, v in self.params.items()},
            "rationale": self.rationale[:280],
            "grounding": list(self.grounding),
            "confidence": float(self.confidence),
            "responds_to": self.responds_to,
        }


@dataclass(slots=True)
class DialogueUtterance:
    """Semantic rendering of a decision — numbers only via ``metrics``."""

    agent_id: str
    role: str
    country: str
    text: str
    locale: str = "en"
    metrics: list[GroundedMetric] = field(default_factory=list)
    responds_to: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "country": self.country,
            "text": self.text,
            "locale": self.locale,
            "metrics": [m.as_dict() for m in self.metrics],
            "responds_to": self.responds_to,
        }


@dataclass(slots=True)
class DialogueTurnBundle:
    tick: int
    decisions: list[DialogueDecision] = field(default_factory=list)
    utterances: list[DialogueUtterance] = field(default_factory=list)
    source: str = "control_law"  # control_law | llm | hybrid | status
    rejected: int = 0
    material_reason: str = ""
    level: str = "info"
    cast: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick": self.tick,
            "decisions": [d.as_dict() for d in self.decisions],
            "utterances": [u.as_dict() for u in self.utterances],
            "source": self.source,
            "rejected": self.rejected,
            "material_reason": self.material_reason,
            "level": self.level,
            "cast": list(self.cast),
        }


def actions_for_role(*, can_set_policy: bool) -> tuple[str, ...]:
    if can_set_policy:
        return tuple(sorted(ACTION_ENUMS))
    return tuple(sorted(_NON_POLICY_ACTIONS))
