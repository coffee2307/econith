"""ECONITH :: core.locale_prefs

Dashboard locale preference mirrored to the backend so LLM narratives
(journalist, world agent exchange) match the active UI language.
"""
from __future__ import annotations

import os

_dashboard_locale: str = "en"


def _env_locale() -> str | None:
    raw = (os.getenv("DASHBOARD_DEFAULT_LOCALE") or "").strip().lower()
    if not raw:
        return None
    return "vi" if raw.startswith("vi") else "en"


def bootstrap_locale_from_env() -> str:
    """Call once at process start so demo boots are Vietnamese without UI sync."""
    env = _env_locale()
    if env is not None:
        set_dashboard_locale(env)
    return dashboard_locale()


def set_dashboard_locale(locale: str) -> None:
    global _dashboard_locale
    _dashboard_locale = "vi" if (locale or "en").lower().startswith("vi") else "en"


def dashboard_locale() -> str:
    # Env wins every read — PowerShell demo boots must not depend on import order
    # or a stale first POST /locale from the dashboard.
    env = _env_locale()
    if env is not None:
        return env
    return _dashboard_locale
