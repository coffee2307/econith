"""ECONITH :: bridges

Integration bridge layer reconciling the legacy runtime (WorldKernel,
ExchangeBridge) with the advanced production subsystems (SovereignWorldGraph,
CCXTBinanceBridge) without read-model or capital-state pollution.
"""
from __future__ import annotations

from bridges.quant_bridge import QuantExecutionBridge
from bridges.world_bridge import WorldBridge

__all__ = ["QuantExecutionBridge", "WorldBridge"]
