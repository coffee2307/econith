"""ECONITH :: core.ingestion

Zero-cost, reputable macroeconomic ingestion topology for the CORE. Binds the
St. Louis Fed (FRED), World Bank, IMF, Eurostat and the open-source ``yfinance``
wrapper into a single frequency-isolated :class:`ExhaustiveContextState`.
"""
from __future__ import annotations

from core.ingestion.config import (
    EurostatConfig,
    FREDConfig,
    IMFConfig,
    IngestionFrequency,
    MacroIngestionSettings,
    SourceConfig,
    SourceKind,
    WorldBankConfig,
    YFinanceConfig,
)
from core.ingestion.context_state import (
    AssetDesk,
    AssetMicroState,
    AssetUniverse,
    ExhaustiveContextState,
    MacroFeatureBlock,
    MicroFeatureBlock,
)
from core.ingestion.macro_hub import MacroIngestionHub

__all__ = [
    "AssetDesk",
    "AssetMicroState",
    "AssetUniverse",
    "EurostatConfig",
    "ExhaustiveContextState",
    "FREDConfig",
    "IMFConfig",
    "IngestionFrequency",
    "MacroFeatureBlock",
    "MacroIngestionHub",
    "MacroIngestionSettings",
    "MicroFeatureBlock",
    "SourceConfig",
    "SourceKind",
    "WorldBankConfig",
    "YFinanceConfig",
]
