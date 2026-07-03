"""ECONITH :: quant

The active multi-asset trading desk. Slices the CORE's CausalContextVector into
per-asset masked observations and routes deterministic execution payloads to the
Binance exchange via CCXT (REALITY) or the WORLD synthetic fill engine
(SIMULATION).
"""
from __future__ import annotations

from quant.ccxt_bridge import CCXTBinanceBridge, FillReport
from quant.context_slicer import (
    BrainSlicingAdapter,
    CausalContextVector,
    DeskPolicyHead,
    SlicedObservation,
)
from quant.payloads import (
    AlgoSlice,
    CCXTOrderPayload,
    ExecutionAlgo,
    ExecutionPayload,
    OrderSide,
    OrderType,
    PositionDelta,
)

__all__ = [
    "AlgoSlice",
    "BrainSlicingAdapter",
    "CCXTBinanceBridge",
    "CCXTOrderPayload",
    "CausalContextVector",
    "DeskPolicyHead",
    "ExecutionAlgo",
    "ExecutionPayload",
    "FillReport",
    "OrderSide",
    "OrderType",
    "PositionDelta",
    "SlicedObservation",
]
