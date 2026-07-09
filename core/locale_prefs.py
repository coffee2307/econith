"""ECONITH :: core.locale_prefs

Dashboard locale preference mirrored to the backend so LLM narratives
(journalist, world agent exchange) match the active UI language.
"""
from __future__ import annotations

_dashboard_locale: str = "en"


def set_dashboard_locale(locale: str) -> None:
    global _dashboard_locale
    _dashboard_locale = "vi" if locale.lower().startswith("vi") else "en"


def dashboard_locale() -> str:
    return _dashboard_locale
