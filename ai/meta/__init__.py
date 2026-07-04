"""ECONITH :: ai.meta

The Meta / Core AI orchestration layer (TIER 4 apex).

Reads the unified multi-frequency cross-asset state (high-frequency coin
order-flow + low-frequency macro/tradfi shifts), fuses it into a single
coherent context, and dynamically recalibrates the behaviour of the downstream
sub-agents: Quant AI (signal bias), Risk AI (thresholds), World AI (scenario
pressure).
"""
from __future__ import annotations

from ai.meta.core_ai import (
    CoreAIOrchestrator,
    CrossAssetContext,
    RiskDirective,
    QuantDirective,
    WorldDirective,
)

__all__ = [
    "CoreAIOrchestrator",
    "CrossAssetContext",
    "RiskDirective",
    "QuantDirective",
    "WorldDirective",
]
