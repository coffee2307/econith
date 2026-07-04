"""ECONITH :: collectors.tradfi_assets

Session-based polling of traditional-finance references (DXY, gold, S&P 500,
crude oil) via the keyless yfinance HTTP chart endpoint.
"""
from __future__ import annotations

from collectors.tradfi_assets.poller import TradFiPoller, TradFiConfig

__all__ = ["TradFiPoller", "TradFiConfig"]
