"""ECONITH :: collectors

TIER 1 — the standalone, zero-ML data-collection deployment unit.

This package is intentionally decoupled from ``ai/``, ``training/`` and the
heavy ``core/`` runtime. It depends only on lightweight networking/data
libraries (polars/pandas, websockets, httpx, pyarrow) so the entire folder can
be copied onto a low-spec remote VPS and run 24/7 without a GPU or the ML stack.

Sub-packages:
    shared/        cross-collector primitives (schemas, partitioning, persistence)
    market_coin/   24/7 high-frequency crypto tick/orderbook ingestion
    macro_global/  scheduled macro-index fetching + historical snapshots
    tradfi_assets/ session-based traditional-market polling
"""
from __future__ import annotations

__all__ = ["shared"]
