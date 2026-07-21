"""ECONITH :: config.settings

Application-wide static settings and the Time Engine speed contract.
Combines the typed environment with constants defined by the master plan.

This module is the single, centralized configuration surface: execution
parameters (``starting_capital``), API security (``api_auth_enabled``,
``api_keys``, protected route prefixes) and the audit-trail sink are all bound
here from :class:`~config.environment.Environment` so every subsystem
(CockpitTelemetryHub, Sentinel, simulation runners, the auth middleware) reads
one coherent contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config.environment import Environment, get_environment

# Time Engine contract (master plan, Phase 0):
# at 1x speed, 1 real-world second == 1 simulated day.
TIME_SPEED_MULTIPLIERS: tuple[int, ...] = (1, 2, 5, 10, 20)
SECONDS_PER_SIM_DAY_AT_1X: int = 1


@dataclass(frozen=True)
class Settings:
    """Immutable aggregate of runtime settings for the platform."""

    env: Environment = field(default_factory=get_environment)

    app_name: str = "ECONITH Quant"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"

    # CORS origins for the dashboard (https://localhost by default).
    cors_origins: tuple[str, ...] = (
        "https://localhost",
        "https://localhost:3000",
        "http://localhost:3000",
        "http://localhost:3001",
    )

    time_speed_multipliers: tuple[int, ...] = TIME_SPEED_MULTIPLIERS

    # -- execution / capital --------------------------------------------------
    @property
    def starting_capital(self) -> float:
        """Principal equity base shared by cockpit, sentinel and simulators."""
        return self.env.starting_capital

    # -- API security ---------------------------------------------------------
    @property
    def api_auth_enabled(self) -> bool:
        return self.env.api_auth_enabled

    @property
    def api_keys(self) -> frozenset[str]:
        return self.env.api_key_set

    @property
    def audit_log_path(self) -> str:
        return self.env.audit_log_path

    @property
    def audit_log_max_bytes(self) -> int:
        return self.env.audit_log_max_bytes

    @property
    def audit_log_backups(self) -> int:
        return self.env.audit_log_backups

    @property
    def social_api_url(self) -> str:
        return self.env.social_api_url.rstrip("/")

    @property
    def social_ui_url(self) -> str:
        return self.env.social_ui_url.rstrip("/")

    @property
    def protected_path_prefixes(self) -> tuple[str, ...]:
        """Sensitive mutating routes guarded by the auth middleware.

        Every entry is a fully-qualified path prefix (``api_prefix`` applied).
        A request whose path starts with any of these AND uses a mutating HTTP
        method must present a valid API key / bearer token.
        """
        p = self.api_prefix
        return (
            f"{p}/mode",
            f"{p}/world/tariff",
            f"{p}/world/mutate",
            f"{p}/world/scenario",
            f"{p}/world/hypotheses",
            f"{p}/world/country",       # covers /world/country/{code}/mutate
            f"{p}/sentinel/inject",
            f"{p}/sentinel/reset",
            f"{p}/time/speed",
            f"{p}/time/pause",
            f"{p}/time/resume",
            f"{p}/order",               # execution-intent injection paths
            f"{p}/quant/routing/profile",
            f"{p}/control/mode",
            f"{p}/control/world-simulation",
            f"{p}/control/world-bridge",
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached singleton accessor for application settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
