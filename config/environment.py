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
    # Three sovereign credential groups — Binance demo and live use different keys.
    # DATA  : market-data collectors / websocket (mainnet public or keyed REST).
    # DEMO  : testnet / paper trading (spot testnet, futures testnet).
    # TRADE : live mainnet execution only.
    binance_data_api_key: str = Field(default="", alias="BINANCE_DATA_API_KEY")
    binance_data_api_secret: str = Field(default="", alias="BINANCE_DATA_API_SECRET")
    binance_demo_api_key: str = Field(default="", alias="BINANCE_DEMO_API_KEY")
    binance_demo_api_secret: str = Field(default="", alias="BINANCE_DEMO_API_SECRET")
    binance_trade_api_key: str = Field(default="", alias="BINANCE_TRADE_API_KEY")
    binance_trade_api_secret: str = Field(default="", alias="BINANCE_TRADE_API_SECRET")
    # ``demo`` routes CCXT to testnet keys; ``live`` routes to mainnet trade keys.
    binance_execution_env: str = Field(default="", alias="BINANCE_EXECUTION_ENV")
    # Deprecated — use BINANCE_EXECUTION_ENV=demo|live. Kept for backward compat.
    binance_testnet: bool = Field(default=True, alias="BINANCE_TESTNET")
    # CCXT market class: ``spot`` or ``future`` / ``swap`` (USDT-M perps).
    binance_ccxt_default_type: str = Field(
        default="future", alias="BINANCE_CCXT_DEFAULT_TYPE"
    )
    # Data plane endpoints (typically mainnet public streams).
    binance_data_rest_base_url: str = Field(
        default="https://api.binance.com", alias="BINANCE_DATA_REST_BASE_URL"
    )
    binance_data_ws_base_url: str = Field(
        default="wss://stream.binance.com:9443/ws", alias="BINANCE_DATA_WS_BASE_URL"
    )
    # Demo / testnet plane (futures testnet by default for Quant desk).
    binance_demo_rest_base_url: str = Field(
        default="https://testnet.binancefuture.com", alias="BINANCE_DEMO_REST_BASE_URL"
    )
    binance_demo_ws_base_url: str = Field(
        default="wss://fstream.binancefuture.com", alias="BINANCE_DEMO_WS_BASE_URL"
    )
    # Live trade plane (mainnet).
    binance_trade_rest_base_url: str = Field(
        default="https://fapi.binance.com", alias="BINANCE_TRADE_REST_BASE_URL"
    )
    binance_trade_ws_base_url: str = Field(
        default="wss://fstream.binance.com", alias="BINANCE_TRADE_WS_BASE_URL"
    )
    # Legacy URL aliases (fall back when the split vars above are untouched).
    binance_rest_base_url: str = Field(
        default="https://testnet.binancefuture.com", alias="BINANCE_REST_BASE_URL"
    )
    binance_ws_base_url: str = Field(
        default="wss://stream.binance.com:9443/ws", alias="BINANCE_WS_BASE_URL"
    )

    # --- Macro ingestion (CORE) ------------------------------------------
    # St. Louis Fed FRED is the single keyed macro source; every other node
    # (World Bank, IMF, Eurostat, yfinance) is keyless/open-access.
    fred_api_key: str = Field(default="", alias="FRED_API_KEY")

    # --- Storage ---------------------------------------------------------
    database_url: str = Field(
        default="sqlite:///./datasets/econith.sqlite", alias="DATABASE_URL"
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    # --- Sentinel risk governance ----------------------------------------
    # Tunable thresholds. Defaults are strict (production). For local dev
    # where latency is naturally higher and equity is synthetic, loosen these.
    sentinel_latency_limit_ms: float = Field(default=2000.0, alias="SENTINEL_LATENCY_LIMIT_MS")
    sentinel_max_drawdown_pct: float = Field(default=0.10, alias="SENTINEL_MAX_DRAWDOWN_PCT")
    sentinel_var_limit_pct: float = Field(default=0.05, alias="SENTINEL_VAR_LIMIT_PCT")
    sentinel_freeze_cooldown_s: float = Field(default=30.0, alias="SENTINEL_FREEZE_COOLDOWN_S")

    # --- Execution / capital ---------------------------------------------
    # Single source of truth for the principal equity base. Bound natively into
    # CockpitTelemetryHub, the Sentinel risk governor, and simulation runners so
    # every read-model agrees on starting capital to the cent.
    starting_capital: float = Field(default=100_000.0, alias="STARTING_CAPITAL")

    # --- AI → execution sizing (Quant desk) --------------------------------
    # Minimum exposure delta before emitting order.intent (lower = more active).
    ai_min_exposure_delta: float = Field(default=0.002, alias="AI_MIN_EXPOSURE_DELTA")
    # Floor confidence used only for position sizing when |direction| is meaningful.
    ai_confidence_floor: float = Field(default=0.12, alias="AI_CONFIDENCE_FLOOR")
    # Target notional (USD) scaled by |delta| before smart-router split.
    ai_base_notional_usd: float = Field(default=2_000.0, alias="AI_BASE_NOTIONAL_USD")
    # Minimum per-leg notional (USD) on futures demo (Binance ~5–10 USDT).
    ai_min_leg_notional_usd: float = Field(default=30.0, alias="AI_MIN_LEG_NOTIONAL_USD")

    # --- API security ----------------------------------------------------
    # When enabled, sensitive mutating routes require an API key / bearer token.
    api_auth_enabled: bool = Field(default=False, alias="API_AUTH_ENABLED")
    # Comma-separated list of accepted API keys / bearer tokens.
    api_keys: str = Field(default="", alias="API_KEYS")
    # Rotating audit-trail sink for every operator state-mutation command.
    audit_log_path: str = Field(default="logs/econith_audit.log", alias="AUDIT_LOG_PATH")
    audit_log_max_bytes: int = Field(default=5_000_000, alias="AUDIT_LOG_MAX_BYTES")
    audit_log_backups: int = Field(default=5, alias="AUDIT_LOG_BACKUPS")

    # --- econith_social (first-party social simulation sidecar) ---------------
    social_api_url: str = Field(
        default="http://localhost:5001", alias="SOCIAL_API_URL"
    )
    social_ui_url: str = Field(
        default="http://localhost:3001", alias="SOCIAL_UI_URL"
    )
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(
        default="https://api.groq.com/openai/v1", alias="LLM_BASE_URL"
    )
    llm_model_name: str = Field(
        default="llama-3.3-70b-versatile", alias="LLM_MODEL_NAME"
    )
    zep_api_key: str = Field(default="", alias="ZEP_API_KEY")

    @property
    def llm_api_key_list(self) -> list[str]:
        """Parsed Groq/OpenAI keys from comma-separated ``LLM_API_KEY``."""
        from core.llm_pool import parse_llm_api_keys

        return parse_llm_api_keys(self.llm_api_key)

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.llm_api_key_list)

    @property
    def effective_llm_api_key(self) -> str:
        """First configured key (backward compatibility)."""
        keys = self.llm_api_key_list
        return keys[0] if keys else ""

    @property
    def api_key_set(self) -> frozenset[str]:
        """Parsed, de-duplicated set of accepted API keys / bearer tokens."""
        return frozenset(
            tok.strip() for tok in self.api_keys.split(",") if tok.strip()
        )

    @property
    def effective_binance_ccxt_default_type(self) -> str:
        """Normalised CCXT ``options.defaultType`` for Binance."""
        raw = (self.binance_ccxt_default_type or "future").strip().lower()
        if raw in ("futures", "linear", "perp", "perpetual"):
            return "future"
        if raw in ("future", "swap", "delivery", "inverse", "spot", "margin"):
            return raw
        return "future"

    @property
    def has_fred_credentials(self) -> bool:
        """True only if a real (non-placeholder) FRED API key is configured."""
        return self._is_real_credential(self.fred_api_key)

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
    def effective_binance_demo_api_key(self) -> str:
        """Resolved demo/testnet key (falls back to legacy trade key)."""
        if self._is_real_credential(self.binance_demo_api_key):
            return self.binance_demo_api_key
        if self._is_real_credential(self.binance_trade_api_key):
            return self.binance_trade_api_key
        return self.binance_api_key

    @property
    def effective_binance_demo_api_secret(self) -> str:
        """Resolved demo/testnet secret (falls back to legacy trade secret)."""
        if self._is_real_credential(self.binance_demo_api_secret):
            return self.binance_demo_api_secret
        if self._is_real_credential(self.binance_trade_api_secret):
            return self.binance_trade_api_secret
        return self.binance_api_secret

    @property
    def effective_binance_trade_api_key(self) -> str:
        """Resolved live mainnet trade key with fallback to legacy variables."""
        if self._is_real_credential(self.binance_trade_api_key):
            return self.binance_trade_api_key
        return self.binance_api_key

    @property
    def effective_binance_trade_api_secret(self) -> str:
        """Resolved live mainnet trade secret with fallback to legacy variables."""
        if self._is_real_credential(self.binance_trade_api_secret):
            return self.binance_trade_api_secret
        return self.binance_api_secret

    @property
    def binance_execution_env_resolved(self) -> str:
        """``demo`` (testnet) or ``live`` (mainnet)."""
        explicit = (self.binance_execution_env or "").strip().lower()
        if explicit in ("demo", "testnet"):
            return "demo"
        if explicit == "live":
            return "live"
        return "demo" if self.binance_testnet else "live"

    @property
    def is_demo_execution(self) -> bool:
        return self.binance_execution_env_resolved == "demo"

    @property
    def effective_binance_execution_api_key(self) -> str:
        """Trade key for the active execution plane (demo vs live)."""
        if self.is_demo_execution:
            return self.effective_binance_demo_api_key
        return self.effective_binance_trade_api_key

    @property
    def effective_binance_execution_api_secret(self) -> str:
        """Trade secret for the active execution plane (demo vs live)."""
        if self.is_demo_execution:
            return self.effective_binance_demo_api_secret
        return self.effective_binance_trade_api_secret

    @property
    def has_binance_demo_credentials(self) -> bool:
        """True when real demo/testnet keys are configured."""
        return self._is_real_credential(
            self.effective_binance_demo_api_key
        ) and self._is_real_credential(self.effective_binance_demo_api_secret)

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
        """True only if real (non-placeholder) live mainnet trade keys exist."""
        return self._is_real_credential(
            self.effective_binance_trade_api_key
        ) and self._is_real_credential(self.effective_binance_trade_api_secret)

    @property
    def has_binance_execution_credentials(self) -> bool:
        """Credentials for the currently selected execution plane."""
        if self.is_demo_execution:
            return self.has_binance_demo_credentials
        return self.has_binance_trade_credentials


@lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Cached singleton accessor for the environment configuration."""
    return Environment()
