"""ECONITH World -- unified macro/micro simulation framework.

The Unified Simulation Kernel closes the bidirectional feedback loop between
ECONITH World (macro geopolitics) and ECONITH Quant (market microstructure):

    macro state  --macro_to_micro-->  MicrostructuralVolatilityVector  --> Quant
    Quant tape   --quant_to_macro-->  MacroFeedback (capital flight, ...) --> World

Public building blocks are re-exported here for convenient wiring/testing.
"""
from ai.simulator_engine.agents import (
    CorporateAI,
    GovernmentAI,
    SocietalSentimentAI,
    default_intelligent_agents,
)
from ai.simulator_engine.cross_impact import (
    MacroFeedback,
    MicrostructuralVolatilityVector,
    macro_to_micro,
    quant_to_macro,
)
from ai.simulator_engine.llm_scenario import LLMScenarioEngine, ScenarioParse
from ai.simulator_engine.market_context import MarketContext, MarketSnapshot
from ai.simulator_engine.narrative import CausalFact, NarrativeEngine
from ai.simulator_engine.world_kernel import WorldKernel

__all__ = [
    "WorldKernel",
    "LLMScenarioEngine",
    "ScenarioParse",
    "MarketContext",
    "MarketSnapshot",
    "MicrostructuralVolatilityVector",
    "MacroFeedback",
    "macro_to_micro",
    "quant_to_macro",
    "CorporateAI",
    "GovernmentAI",
    "SocietalSentimentAI",
    "default_intelligent_agents",
    "NarrativeEngine",
    "CausalFact",
]
