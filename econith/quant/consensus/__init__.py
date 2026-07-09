"""ECONITH :: econith.quant.consensus

Native multi-agent debate consensus (TradingAgents internalization).
"""

from econith.quant.consensus.kernel import (
    AgentVote,
    ConsensusVerdict,
    EconithConsensusKernel,
)

__all__ = ["AgentVote", "ConsensusVerdict", "EconithConsensusKernel"]
