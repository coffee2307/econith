"""ECONITH :: collectors.market_coin

24/7 high-frequency crypto tick/orderbook ingestion for the standalone VPS unit.
"""
from __future__ import annotations

from collectors.market_coin.daemon import MarketCoinDaemon, MarketCoinConfig

__all__ = ["MarketCoinDaemon", "MarketCoinConfig"]
