"""ECONITH :: ai.explainability.shap

Backward-compatible alias for :mod:`ai.explainability.attribution`.

The Phase 2 implementation is weighted feature attribution, **not** SHAP.
Prefer importing from ``ai.explainability.attribution`` in new code.
"""
from __future__ import annotations

from ai.explainability.attribution import attribution_to_json, build_attribution

__all__ = ["build_attribution", "attribution_to_json"]
