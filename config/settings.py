"""ECONITH :: config.settings

Application-wide static settings and the Time Engine speed contract.
Combines the typed environment with constants defined by the master plan.
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
    )

    time_speed_multipliers: tuple[int, ...] = TIME_SPEED_MULTIPLIERS


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached singleton accessor for application settings."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
