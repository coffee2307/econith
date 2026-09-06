"""ECONITH :: ai.agents.base

Common contract for all trading agents (master plan, Phase 2, Step 1).

Phase 2 ships deterministic, feature-driven *policy stubs* that conform to the
same interface a trained PPO policy will implement. ``act()`` maps a feature
vector to a bounded directional signal in ``[-1, +1]`` with a confidence in
``[0, 1]``. Swapping in a real ``torch`` PPO network later means only replacing
the body of ``act()`` -- the orchestration, ensemble and Sentinel veto stay put.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class AgentSignal:
    """A single agent's directional opinion at time t."""

    agent: str
    direction: float          # -1.0 (full short) .. +1.0 (full long)
    confidence: float         # 0.0 .. 1.0
    rationale: str = ""
    contributions: dict[str, float] = field(default_factory=dict)


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class BaseAgent(ABC):
    """Abstract policy. Concrete agents implement ``act``."""

    name: str = "base"
    #: feature keys this agent consumes (used by the explainability layer)
    feature_keys: tuple[str, ...] = ()

    @abstractmethod
    def act(self, features: dict[str, Any]) -> AgentSignal:
        """Map a feature dict to a bounded signal. Must never raise."""
        raise NotImplementedError

    @staticmethod
    def _f(features: dict[str, Any], key: str, default: float = 0.0) -> float:
        value = features.get(key)
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default

    @staticmethod
    def clamp(value: float) -> float:
        return _clamp(value)
