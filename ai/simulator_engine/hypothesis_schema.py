"""ECONITH :: ai.simulator_engine.hypothesis_schema

Structured records for autonomous World shock hypotheses and measured outcomes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Hypothesis(BaseModel):
    """A self-authored macro shock to inject via the scenario engine."""

    id: str
    prompt: str
    rationale: str = ""
    severity: float = Field(default=0.5, ge=0.0, le=1.0)
    category: str = "generic"
    subjects: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    generator: Literal["llm", "combinatorial"] = "combinatorial"


class HypothesisOutcome(BaseModel):
    """Measured pre/post World deltas for one hypothesis cycle."""

    hypothesis_id: str
    status: Literal["ok", "skipped", "error"] = "ok"
    tick_span: int = 0
    settle_sec: float = 0.0
    pre_macro: dict[str, Any] = Field(default_factory=dict)
    post_macro: dict[str, Any] = Field(default_factory=dict)
    deltas: dict[str, float] = Field(default_factory=dict)
    micro_summary: dict[str, Any] = Field(default_factory=dict)
    scenario: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    finished_at: datetime = Field(default_factory=_utc_now)


class HypothesisReport(BaseModel):
    """Rolling history of autonomous hypothesis runs."""

    runs: list[HypothesisOutcome] = Field(default_factory=list)
    total_ok: int = 0
    total_skipped: int = 0
    total_error: int = 0
    last_hypothesis: Hypothesis | None = None

    def append(self, hypothesis: Hypothesis, outcome: HypothesisOutcome, *, maxlen: int = 50) -> None:
        self.runs.append(outcome)
        if len(self.runs) > maxlen:
            self.runs = self.runs[-maxlen:]
        if outcome.status == "ok":
            self.total_ok += 1
        elif outcome.status == "skipped":
            self.total_skipped += 1
        else:
            self.total_error += 1
        self.last_hypothesis = hypothesis

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
