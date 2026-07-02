"""ECONITH :: config.environment

Loads and validates environment variables from the mounted ``.env`` file.
This is the single source of truth for runtime configuration -- nothing else
in the codebase should read ``os.environ`` directly.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Environment(BaseSettings):
    """Typed view over the process environment / ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -----------------------------------------------------
    app_env: AppEnv = Field(default=AppEnv.DEVELOPMENT, alias="APP_ENV")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Binance API -----------------------------------------------------
    # Legacy single-key pair (kept for backward compatibility).
    binance_api_key: str = Field(default="", alias="BINANCE_API_KEY")
    binance_api_secret: str = Field(default="", alias="BINANCE_API_SECRET")
    # Split credentials: dedicated keys for data ingestion and trade execution.
    binance_data_api_key: str = Field(default="", alias="BINANCE_DATA_API_KEY")
    binance_data_api_secret: str = Field(default="", alias="BINANCE_DATA_API_SECRET")
    binance_trade_api_key: str = Field(default="", alias="BINANCE_TRADE_API_KEY")
    binance_trade_api_secret: str = Field(default="", alias="BINANCE_TRADE_API_SECRET")
    binance_testnet: bool = Field(default=True, alias="BINANCE_TESTNET")
    binance_rest_base_url: str = Field(
        default="https://testnet.binance.vision", alias="BINANCE_REST_BASE_URL"
    )
    binance_ws_base_url: str = Field(
        default="wss://stream.binance.com:9443/ws", alias="BINANCE_WS_BASE_URL"
    )

    # --- Storage ---------------------------------------------------------
    database_url: str = Field(
        default="sqlite:///./datasets/econith.sqlite", alias="DATABASE_URL"
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    @property
    def is_production(self) -> bool:
        return self.app_env == AppEnv.PRODUCTION

    @property
    def has_binance_credentials(self) -> bool:
        """Backward-compatible alias for data-credential availability."""
        return self.has_binance_data_credentials

    @staticmethod
    def _is_real_credential(value: str) -> bool:
        v = (value or "").strip().lower()
        return bool(v) and not v.startswith("your_") and "here" not in v

    @property
    def effective_binance_data_api_key(self) -> str:
        """Resolved data key with fallback to legacy variables."""
        if self._is_real_credential(self.binance_data_api_key):
            return self.binance_data_api_key
        return self.binance_api_key

    @property
    def effective_binance_data_api_secret(self) -> str:
        """Resolved data secret with fallback to legacy variables."""
        if self._is_real_credential(self.binance_data_api_secret):
            return self.binance_data_api_secret
        return self.binance_api_secret

    @property
    def effective_binance_trade_api_key(self) -> str:
        """Resolved trade key with fallback to legacy variables."""
        if self._is_real_credential(self.binance_trade_api_key):
            return self.binance_trade_api_key
        return self.binance_api_key

    @property
    def effective_binance_trade_api_secret(self) -> str:
        """Resolved trade secret with fallback to legacy variables."""
        if self._is_real_credential(self.binance_trade_api_secret):
            return self.binance_trade_api_secret
        return self.binance_api_secret

    @property
    def has_binance_data_credentials(self) -> bool:
        """True only if real (non-placeholder) Binance data keys are configured.

        The shipped ``.env.example`` carries placeholder values like
        ``your_testnet_api_key_here``; these must NOT trip the system into LIVE
        mode. Treat empty or obviously templated values as "no credentials" so
        the platform stays mock-first out of the box.
        """
        return self._is_real_credential(
            self.effective_binance_data_api_key
        ) and self._is_real_credential(self.effective_binance_data_api_secret)

    @property
    def has_binance_trade_credentials(self) -> bool:
        """True only if real (non-placeholder) Binance trade keys are configured."""
        return self._is_real_credential(
            self.effective_binance_trade_api_key
        ) and self._is_real_credential(self.effective_binance_trade_api_secret)


@lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Cached singleton accessor for the environment configuration."""
    return Environment()
