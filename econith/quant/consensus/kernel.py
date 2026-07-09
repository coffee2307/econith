"""ECONITH :: econith.quant.consensus.kernel

Native multi-agent debate consensus — internalizes the TradingAgents pattern
(Macro / Technical / Sentiment analysts debating to a weighted verdict) as pure
ECONITH logic that runs synchronously inside the TickPipeline cadence.

No external package, no LLM dependency, no threads: a deterministic council of
transparent analysts votes each cadence; the fused verdict is ADVISORY only and
is published on ``meta.debate.verdict``. It never produces ``order.intent`` and
never claims execution authority — the Sentinel-gated chain remains sovereign.

Compatibility surface used by the orchestrator:
    resolve(ctx_snapshot) -> ConsensusVerdict     (async; publishes verdict)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from core.event_bus import EventBus

logger = logging.getLogger("econith.quant.consensus")

__all__ = ["AgentVote", "ConsensusVerdict", "EconithConsensusKernel"]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(slots=True)
class AgentVote:
    agent: str
    bias: float          # [-1, 1]
    confidence: float    # [0, 1]
    rationale: str = ""

    def payload(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "bias": round(self.bias, 4),
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class ConsensusVerdict:
    """Blended directional lean produced by the analyst council."""

    bias: float = 0.0                 # [-1, 1] additive directional lean
    confidence: float = 0.0           # [0, 1]
    sources: list[str] = field(default_factory=list)
    votes: list[AgentVote] = field(default_factory=list)
    dissent: dict[str, float] = field(default_factory=dict)

    @property
    def has_signal(self) -> bool:
        return bool(self.sources)

    def payload(self) -> dict[str, Any]:
        return {
            "consensus_bias": round(self.bias, 4),
            "consensus_confidence": round(self.confidence, 4),
            "sources": list(self.sources),
            "votes": [v.payload() for v in self.votes],
            "dissent": {k: round(v, 4) for k, v in self.dissent.items()},
        }


class EconithConsensusKernel:
    """Deterministic analyst council. Emits an advisory verdict per cadence."""

    def __init__(self, bus: Optional[EventBus] = None, *, rounds: int = 2) -> None:
        self._bus = bus
        self._rounds = rounds

    def register(self) -> None:
        # Pure kernel: no bus subscriptions. Driven by the orchestrator cadence.
        return None

    # -- council votes --------------------------------------------------------
    @staticmethod
    def _votes(ctx: dict[str, Any]) -> list[AgentVote]:
        obi = float(ctx.get("obi") or 0.0)
        spread = ctx.get("yield_spread_10y_2y")
        vol = float(ctx.get("realized_vol") or 0.0)
        world_shock = float(ctx.get("world_shock") or 0.0)

        macro_bias = math.tanh((float(spread) if spread is not None else 0.0) * 5.0)
        tech_bias = math.tanh(obi)
        sent_bias = -math.tanh(vol * 30.0 + world_shock)  # turbulence -> risk-off
        return [
            AgentVote("MacroAnalyst", macro_bias, 0.6, "yield-curve lean"),
            AgentVote("TechnicalAnalyst", tech_bias, 0.7, "order-book imbalance"),
            AgentVote("SentimentAnalyst", sent_bias, 0.5, "realised-vol / world fear"),
        ]

    def deliberate(self, ctx: dict[str, Any]) -> ConsensusVerdict:
        """Run the weighted debate over a context snapshot."""
        votes = self._votes(ctx)
        conf_sum = sum(v.confidence for v in votes) or 1.0
        consensus_bias = sum(v.bias * v.confidence for v in votes) / conf_sum
        consensus_conf = conf_sum / len(votes)
        dissent = {v.agent: v.bias - consensus_bias for v in votes}
        return ConsensusVerdict(
            bias=_clamp(consensus_bias, -1.0, 1.0),
            confidence=_clamp(consensus_conf, 0.0, 1.0),
            sources=["econith_council"],
            votes=votes,
            dissent=dissent,
        )

    async def resolve(self, ctx_snapshot: dict[str, Any]) -> ConsensusVerdict:
        """Deliberate + publish the advisory verdict (mode-agnostic, advisory)."""
        verdict = self.deliberate(ctx_snapshot)
        if self._bus is not None and verdict.has_signal:
            try:
                await self._bus.publish("meta.debate.verdict", **verdict.payload())
            except Exception:  # noqa: BLE001 - telemetry publish must not break loop
                logger.debug("consensus verdict publish failed")
        return verdict
