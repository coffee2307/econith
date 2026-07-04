"""ECONITH :: collectors.macro_global

Low-frequency macro-index ingestion (FRED + keyless open institutional sources)
with append-only point-in-time snapshot persistence.
"""
from __future__ import annotations

from collectors.macro_global.scheduler import MacroScheduler, MacroSourceSpec

__all__ = ["MacroScheduler", "MacroSourceSpec"]
