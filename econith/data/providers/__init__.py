"""ECONITH :: econith.data.providers

Lightweight, dependency-free market/macro data adapters (OpenBB internalization).
The heavy vendor SDK is never imported; adapters normalize whatever raw rows a
caller supplies (or fetches via httpx elsewhere) into a single ECONITH schema.
"""

from econith.data.providers.base import DataSeriesRow, MacroProvider, TradfiProvider

__all__ = ["DataSeriesRow", "MacroProvider", "TradfiProvider"]
