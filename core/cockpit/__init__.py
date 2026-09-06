"""ECONITH :: core.cockpit

Aviation-cockpit telemetry read-model, Pydantic contracts and FastAPI streaming
endpoints for the Next-Gen Quant Cockpit dashboard.
"""
from __future__ import annotations

from core.cockpit.schemas import (
    AllocationCell,
    AssetAllocationRadar,
    CockpitNewsLine,
    CockpitTelemetryFrame,
    DeskTier,
    MacroContextStrip,
    MarginSecurityMatrix,
    MatchedOrderLog,
    PnLTelemetryHUD,
)
from core.cockpit.ws import CockpitTelemetryHub, build_cockpit_router

__all__ = [
    "AllocationCell",
    "AssetAllocationRadar",
    "CockpitNewsLine",
    "CockpitTelemetryFrame",
    "CockpitTelemetryHub",
    "DeskTier",
    "MacroContextStrip",
    "MarginSecurityMatrix",
    "MatchedOrderLog",
    "PnLTelemetryHUD",
    "build_cockpit_router",
]
