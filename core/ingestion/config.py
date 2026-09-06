"""ECONITH :: core.ingestion.config

Strict Pydantic V2 configuration contracts for the CORE's zero-cost, reputable
macroeconomic ingestion topology.

Every source in the ingestion mesh is expressed as a typed, self-validating
configuration object. The design mandate is *institutional-grade & zero-cost*:
we bind exclusively to public-interest institutional databases (St. Louis Fed
FRED, World Bank, IMF, Eurostat) and the community-maintained ``yfinance``
wrapper. No commercial freemium keys, no proprietary tiers.

All models inherit :class:`SourceConfig`, giving the :class:`MacroIngestionHub`
a homogeneous contract to schedule, throttle and health-check each adapter.
"""
from __future__ import annotations

from datetime import timedelta
from enum import Enum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

__all__ = [
    "IngestionFrequency",
    "SourceKind",
    "SourceConfig",
    "FREDConfig",
    "WorldBankConfig",
    "IMFConfig",
    "EurostatConfig",
    "YFinanceConfig",
    "MacroIngestionSettings",
]


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class IngestionFrequency(str, Enum):
    """Cadence class of a data source, used to route it into the correct
    frequency-isolated tokenizer (macro-low vs micro-high)."""

    REALTIME = "realtime"          # sub-second / streaming (reserved, micro plane)
    INTRADAY = "intraday"          # minutes-to-hours (yfinance spot polling)
    DAILY = "daily"                # once per trading day
    WEEKLY = "weekly"
    MONTHLY = "monthly"            # CPI, unemployment, IP
    QUARTERLY = "quarterly"        # GDP, debt-to-GDP

    @property
    def poll_interval(self) -> timedelta:
        """Recommended scheduler back-off between successive pulls."""
        return {
            IngestionFrequency.REALTIME: timedelta(seconds=1),
            IngestionFrequency.INTRADAY: timedelta(minutes=5),
            IngestionFrequency.DAILY: timedelta(hours=6),
            IngestionFrequency.WEEKLY: timedelta(days=1),
            IngestionFrequency.MONTHLY: timedelta(days=1),
            IngestionFrequency.QUARTERLY: timedelta(days=1),
        }[self]


class SourceKind(str, Enum):
    """Discriminator identifying which adapter services a config."""

    FRED = "fred"
    WORLD_BANK = "world_bank"
    IMF = "imf"
    EUROSTAT = "eurostat"
    YFINANCE = "yfinance"


# ---------------------------------------------------------------------------
# Base contract
# ---------------------------------------------------------------------------
class SourceConfig(BaseModel):
    """Common, self-validating contract shared by every ingestion source."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    kind: SourceKind
    enabled: bool = True
    frequency: IngestionFrequency = IngestionFrequency.DAILY
    request_timeout_s: Annotated[float, Field(gt=0, le=120)] = 30.0
    max_retries: Annotated[int, Field(ge=0, le=10)] = 4
    backoff_base_s: Annotated[float, Field(gt=0, le=30)] = 1.5
    # Namespace prefix under which this source publishes onto the EventBus,
    # e.g. "core.macro.fred". Keeps low-frequency macro strictly namespaced
    # away from the "md.*"/"indicator.*" micro plane.
    topic_namespace: str = Field(default="core.macro", min_length=1)

    @property
    def is_macro(self) -> bool:
        """Everything but the realtime/intraday micro plane is macro-low."""
        return self.frequency not in (
            IngestionFrequency.REALTIME,
            IngestionFrequency.INTRADAY,
        )


# ---------------------------------------------------------------------------
# St. Louis Fed :: FRED
# ---------------------------------------------------------------------------
class FREDConfig(SourceConfig):
    """Native configuration for the St. Louis Fed FRED API.

    The ``FRED_API_KEY`` is the single explicit credential in the entire macro
    topology (FRED requires a free registered key; every other source here is
    keyless). Series IDs default to the canonical US monetary/real-economy set.
    """

    kind: SourceKind = SourceKind.FRED
    frequency: IngestionFrequency = IngestionFrequency.DAILY
    topic_namespace: str = "core.macro.fred"

    api_key: str = Field(
        default="",
        alias="FRED_API_KEY",
        description="Free St. Louis Fed API key. Registered at fred.stlouisfed.org.",
    )
    base_url: HttpUrl = Field(default="https://api.stlouisfed.org/fred")
    file_type: str = Field(default="json")

    # Canonical FRED series -> semantic feature name. These are the exact
    # institutional monetary metrics the CORE tokenizes as the macro spine.
    series: dict[str, str] = Field(
        default_factory=lambda: {
            "FEDFUNDS": "fed_funds_effective_rate",
            "CPIAUCSL": "consumer_price_index",
            "PCEPILFE": "core_pce",
            "UNRATE": "unemployment_rate",
            "T10Y2Y": "yield_spread_10y_2y",
            "INDPRO": "industrial_production",
            "DGS10": "treasury_10y_yield",
            "DGS2": "treasury_2y_yield",
        }
    )

    @field_validator("file_type")
    @classmethod
    def _validate_file_type(cls, v: str) -> str:
        if v not in ("json", "xml"):
            raise ValueError("FRED file_type must be 'json' or 'xml'")
        return v

    @model_validator(mode="after")
    def _warn_missing_key(self) -> "FREDConfig":
        # A placeholder / empty key means the adapter runs in degraded (mock)
        # mode rather than raising -- the platform must boot key-less.
        return self

    @property
    def has_credentials(self) -> bool:
        v = (self.api_key or "").strip().lower()
        return bool(v) and not v.startswith("your_") and "here" not in v


# ---------------------------------------------------------------------------
# World Bank :: keyless open API
# ---------------------------------------------------------------------------
class WorldBankConfig(SourceConfig):
    """World Bank Open Data -- fully keyless public endpoints.

    Streams sovereign structural parameters: debt-to-GDP, trade balance,
    GDP growth, current-account and reserves for the tracked country set.
    """

    kind: SourceKind = SourceKind.WORLD_BANK
    frequency: IngestionFrequency = IngestionFrequency.QUARTERLY
    topic_namespace: str = "core.macro.worldbank"
    base_url: HttpUrl = Field(default="https://api.worldbank.org/v2")
    per_page: Annotated[int, Field(ge=1, le=1000)] = 200

    # ISO-3 country codes tracked against the WORLD sovereign graph.
    countries: tuple[str, ...] = ("USA", "CHN", "JPN", "DEU", "VNM", "GBR")
    # World Bank indicator code -> semantic feature name.
    indicators: dict[str, str] = Field(
        default_factory=lambda: {
            "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct",
            "GC.DOD.TOTL.GD.ZS": "debt_to_gdp",
            "NE.RSB.GNFS.ZS": "trade_balance_pct_gdp",
            "FI.RES.TOTL.CD": "foreign_reserves_usd",
            "BN.CAB.XOKA.GD.ZS": "current_account_pct_gdp",
        }
    )


# ---------------------------------------------------------------------------
# IMF :: keyless SDMX JSON
# ---------------------------------------------------------------------------
class IMFConfig(SourceConfig):
    """IMF ``SDMX-JSON`` data service -- keyless, public-interest.

    Captures cross-border macro-structural series (IFS/DOT datasets) such as
    exchange rates, reserves and directional trade flows.
    """

    kind: SourceKind = SourceKind.IMF
    frequency: IngestionFrequency = IngestionFrequency.MONTHLY
    topic_namespace: str = "core.macro.imf"
    base_url: HttpUrl = Field(
        default="https://dataservices.imf.org/REST/SDMX_JSON.svc"
    )
    # dataset -> mapping of IMF indicator code to semantic feature name.
    datasets: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {
            "IFS": {
                "ENDA_XDC_USD_RATE": "fx_period_avg",
                "FILR_PA": "policy_rate",
            },
            "DOT": {
                "TXG_FOB_USD": "exports_fob_usd",
                "TMG_CIF_USD": "imports_cif_usd",
            },
        }
    )
    countries: tuple[str, ...] = ("US", "CN", "JP", "DE", "VN", "GB")


# ---------------------------------------------------------------------------
# Eurostat :: keyless statistics bridge
# ---------------------------------------------------------------------------
class EurostatConfig(SourceConfig):
    """Eurostat statistics API bridge -- free, open-access JSON-stat 2.0.

    Captures European macro aggregates: HICP (regional CPI), industrial
    sentiment and unemployment for the euro area.
    """

    kind: SourceKind = SourceKind.EUROSTAT
    frequency: IngestionFrequency = IngestionFrequency.MONTHLY
    topic_namespace: str = "core.macro.eurostat"
    base_url: HttpUrl = Field(
        default="https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
    )
    # Eurostat dataset code -> semantic feature name.
    datasets: dict[str, str] = Field(
        default_factory=lambda: {
            "prc_hicp_midx": "euro_hicp_index",
            "ei_bsin_m_r2": "euro_industrial_sentiment",
            "une_rt_m": "euro_unemployment_rate",
        }
    )
    geo: tuple[str, ...] = ("EA20", "DE", "FR", "IT", "ES")


# ---------------------------------------------------------------------------
# yfinance :: open-source TradFi telemetry
# ---------------------------------------------------------------------------
class YFinanceConfig(SourceConfig):
    """Open-source Yahoo Finance wrapper for global spot telemetry.

    Zero subscription tiers -- tracks commodity spot, equity indices and the
    US Dollar Index. This is the only *intraday* macro-adjacent source and is
    still routed through the macro namespace (never into the micro order-flow
    plane) to preserve epistemic feature isolation.
    """

    kind: SourceKind = SourceKind.YFINANCE
    frequency: IngestionFrequency = IngestionFrequency.INTRADAY
    topic_namespace: str = "core.macro.yfinance"
    interval: str = Field(default="1h")
    lookback_period: str = Field(default="5d")
    # Yahoo ticker -> semantic feature name.
    tickers: dict[str, str] = Field(
        default_factory=lambda: {
            "GC=F": "gold_xau_spot",
            "SI=F": "silver_xag_spot",
            "CL=F": "wti_crude_spot",
            "BZ=F": "brent_crude_spot",
            "DX-Y.NYB": "dollar_index_dxy",
            "^GSPC": "sp500_index",
            "^IXIC": "nasdaq100_index",
            "^HSI": "hang_seng_index",
            "^TNX": "us10y_yield",
        }
    )

    @field_validator("interval")
    @classmethod
    def _validate_interval(cls, v: str) -> str:
        allowed = {"1m", "5m", "15m", "30m", "60m", "1h", "1d", "1wk", "1mo"}
        if v not in allowed:
            raise ValueError(f"yfinance interval must be one of {sorted(allowed)}")
        return v


# ---------------------------------------------------------------------------
# Aggregate settings envelope
# ---------------------------------------------------------------------------
class MacroIngestionSettings(BaseModel):
    """Top-level envelope binding every source config for the hub."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    fred: FREDConfig = Field(default_factory=FREDConfig)
    world_bank: WorldBankConfig = Field(default_factory=WorldBankConfig)
    imf: IMFConfig = Field(default_factory=IMFConfig)
    eurostat: EurostatConfig = Field(default_factory=EurostatConfig)
    yfinance: YFinanceConfig = Field(default_factory=YFinanceConfig)

    def enabled_sources(self) -> list[SourceConfig]:
        """All source configs currently flagged ``enabled``."""
        candidates: list[SourceConfig] = [
            self.fred,
            self.world_bank,
            self.imf,
            self.eurostat,
            self.yfinance,
        ]
        return [c for c in candidates if c.enabled]

    @classmethod
    def from_environment(cls) -> "MacroIngestionSettings":
        """Build settings, injecting ``FRED_API_KEY`` from the typed env."""
        from config.environment import get_environment

        env = get_environment()
        return cls(fred=FREDConfig(FRED_API_KEY=env.fred_api_key))
