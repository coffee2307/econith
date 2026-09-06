"""ECONITH :: core.ingestion.adapters

Modular, fault-tolerant ingestion adapters for each zero-cost institutional
macro source. Every adapter implements the same :class:`IngestionAdapter`
contract so the :class:`~core.ingestion.macro_hub.MacroIngestionHub` can drive
them uniformly with a shared exponential-backoff retry envelope.

Design constraints:
* Adapters are transport-agnostic: they accept an injected async ``fetch``
  callable (default uses ``httpx`` if installed) so they are unit-testable and
  degrade gracefully to a deterministic mock when a dependency/key is absent.
* Each adapter returns a flat ``dict[str, float]`` of *semantic feature name ->
  value*, ready to be folded into an :class:`ExhaustiveContextState`.
"""
from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from core.ingestion.config import (
    EurostatConfig,
    FREDConfig,
    IMFConfig,
    SourceConfig,
    WorldBankConfig,
    YFinanceConfig,
)

logger = logging.getLogger("econith.core.ingestion.adapters")

__all__ = [
    "FetchResult",
    "AsyncFetch",
    "IngestionAdapter",
    "FREDAdapter",
    "WorldBankAdapter",
    "IMFAdapter",
    "EurostatAdapter",
    "YFinanceAdapter",
    "build_adapter",
]

FetchResult = dict[str, Any]
#: An injected transport: given (url, params) return parsed JSON.
AsyncFetch = Callable[[str, dict[str, Any]], Awaitable[FetchResult]]


async def _default_fetch(url: str, params: dict[str, Any]) -> FetchResult:
    """Default HTTP transport using ``httpx`` if available.

    Raises ``RuntimeError`` when ``httpx`` is not installed so the adapter can
    fall back to its deterministic mock path.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("httpx not installed") from exc

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


class IngestionAdapter(ABC):
    """Abstract, self-healing adapter with exponential-backoff retries."""

    def __init__(self, config: SourceConfig, fetch: AsyncFetch | None = None) -> None:
        self.config = config
        self._fetch = fetch or _default_fetch
        self._rng = random.Random(hash(config.kind.value) & 0xFFFF)
        self.last_provenance: str = "unknown"  # live | mock
        self.last_provenance_reason: str = ""

    @property
    def topic(self) -> str:
        return f"{self.config.topic_namespace}.update"

    async def collect(self) -> dict[str, float]:
        """Fetch & normalise this source into semantic features.

        Wraps :meth:`_collect_once` in the retry envelope; on total failure it
        returns the deterministic :meth:`_mock` payload so the CORE never stalls.
        Provenance is recorded on ``last_provenance`` / ``last_provenance_reason``.
        """
        attempt = 0
        while True:
            try:
                features = await self._collect_once()
                self.last_provenance = "live"
                self.last_provenance_reason = ""
                return features
            except Exception as exc:  # noqa: BLE001 - resilience is the contract
                attempt += 1
                if attempt > self.config.max_retries:
                    logger.warning(
                        "%s exhausted retries (%s); serving mock", self.config.kind.value, exc
                    )
                    self.last_provenance = "mock"
                    self.last_provenance_reason = f"{type(exc).__name__}: {exc}"
                    return self._mock()
                delay = self.config.backoff_base_s * (2 ** (attempt - 1))
                jitter = self._rng.uniform(0.0, delay * 0.25)
                logger.debug(
                    "%s attempt %d failed (%s); backing off %.2fs",
                    self.config.kind.value, attempt, exc, delay + jitter,
                )
                await asyncio.sleep(delay + jitter)

    @abstractmethod
    async def _collect_once(self) -> dict[str, float]:
        """Single fetch+parse attempt. Raise on any failure to trigger backoff."""

    @abstractmethod
    def _mock(self) -> dict[str, float]:
        """Deterministic, plausible fallback payload for degraded operation."""


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------
class FREDAdapter(IngestionAdapter):
    config: FREDConfig

    def __init__(self, config: FREDConfig, fetch: AsyncFetch | None = None) -> None:
        super().__init__(config, fetch)

    async def _collect_once(self) -> dict[str, float]:
        if not self.config.has_credentials:
            raise RuntimeError("FRED_API_KEY absent")
        out: dict[str, float] = {}
        base = str(self.config.base_url)
        for series_id, feature in self.config.series.items():
            data = await self._fetch(
                f"{base}/series/observations",
                {
                    "series_id": series_id,
                    "api_key": self.config.api_key,
                    "file_type": self.config.file_type,
                    "sort_order": "desc",
                    "limit": 1,
                },
            )
            observations = data.get("observations") or []
            if observations:
                raw = observations[0].get("value")
                if raw not in (None, ".", ""):
                    out[feature] = float(raw)
        if not out:
            raise RuntimeError("FRED returned no usable observations")
        return out

    def _mock(self) -> dict[str, float]:
        return {
            "fed_funds_effective_rate": 5.33,
            "consumer_price_index": 314.2,
            "core_pce": 2.8,
            "unemployment_rate": 4.1,
            "yield_spread_10y_2y": -0.35,
            "industrial_production": 103.1,
            "treasury_10y_yield": 4.28,
            "treasury_2y_yield": 4.63,
        }


# ---------------------------------------------------------------------------
# World Bank
# ---------------------------------------------------------------------------
class WorldBankAdapter(IngestionAdapter):
    config: WorldBankConfig

    def __init__(self, config: WorldBankConfig, fetch: AsyncFetch | None = None) -> None:
        super().__init__(config, fetch)

    async def _collect_once(self) -> dict[str, float]:
        out: dict[str, float] = {}
        base = str(self.config.base_url)
        for country in self.config.countries:
            for indicator, feature in self.config.indicators.items():
                data = await self._fetch(
                    f"{base}/country/{country}/indicator/{indicator}",
                    {"format": "json", "per_page": self.config.per_page, "mrnev": 1},
                )
                value = self._latest_value(data)
                if value is not None:
                    out[f"{feature}.{country}"] = value
        if not out:
            raise RuntimeError("World Bank returned no usable data")
        return out

    @staticmethod
    def _latest_value(data: Any) -> float | None:
        # World Bank returns [metadata, [observations...]].
        if not isinstance(data, list) or len(data) < 2 or not data[1]:
            return None
        for row in data[1]:
            if row.get("value") is not None:
                return float(row["value"])
        return None

    def _mock(self) -> dict[str, float]:
        return {
            "gdp_growth_pct.USA": 2.5, "debt_to_gdp.USA": 122.3,
            "gdp_growth_pct.CHN": 4.8, "debt_to_gdp.CHN": 83.6,
            "gdp_growth_pct.VNM": 6.1, "trade_balance_pct_gdp.VNM": 4.2,
        }


# ---------------------------------------------------------------------------
# IMF
# ---------------------------------------------------------------------------
class IMFAdapter(IngestionAdapter):
    config: IMFConfig

    def __init__(self, config: IMFConfig, fetch: AsyncFetch | None = None) -> None:
        super().__init__(config, fetch)

    async def _collect_once(self) -> dict[str, float]:
        out: dict[str, float] = {}
        base = str(self.config.base_url)
        for dataset, indicators in self.config.datasets.items():
            for country in self.config.countries:
                key = f"M.{country}." + "+".join(indicators.keys())
                data = await self._fetch(
                    f"{base}/CompactData/{dataset}/{key}", {}
                )
                parsed = self._parse_compact(data, indicators, country)
                out.update(parsed)
        if not out:
            raise RuntimeError("IMF returned no usable data")
        return out

    @staticmethod
    def _parse_compact(
        data: Any, indicators: dict[str, str], country: str
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            series = data["CompactData"]["DataSet"]["Series"]
        except (KeyError, TypeError):
            return out
        rows = series if isinstance(series, list) else [series]
        for row in rows:
            code = row.get("@INDICATOR")
            feature = indicators.get(code)
            obs = row.get("Obs")
            if not feature or not obs:
                continue
            last = obs[-1] if isinstance(obs, list) else obs
            raw = last.get("@OBS_VALUE")
            if raw not in (None, ""):
                out[f"{feature}.{country}"] = float(raw)
        return out

    def _mock(self) -> dict[str, float]:
        return {
            "fx_period_avg.CN": 7.24, "policy_rate.CN": 3.35,
            "exports_fob_usd.VN": 3.4e10, "imports_cif_usd.VN": 3.1e10,
        }


# ---------------------------------------------------------------------------
# Eurostat
# ---------------------------------------------------------------------------
class EurostatAdapter(IngestionAdapter):
    config: EurostatConfig

    def __init__(self, config: EurostatConfig, fetch: AsyncFetch | None = None) -> None:
        super().__init__(config, fetch)

    async def _collect_once(self) -> dict[str, float]:
        out: dict[str, float] = {}
        base = str(self.config.base_url)
        for dataset, feature in self.config.datasets.items():
            data = await self._fetch(
                f"{base}/{dataset}",
                {"format": "JSON", "geo": ",".join(self.config.geo), "lastTimePeriod": 1},
            )
            value = self._first_value(data)
            if value is not None:
                out[feature] = value
        if not out:
            raise RuntimeError("Eurostat returned no usable data")
        return out

    @staticmethod
    def _first_value(data: Any) -> float | None:
        # JSON-stat 2.0: value is a dict {flat_index: number}.
        values = (data or {}).get("value")
        if isinstance(values, dict) and values:
            return float(next(iter(values.values())))
        if isinstance(values, list) and values:
            for v in values:
                if v is not None:
                    return float(v)
        return None

    def _mock(self) -> dict[str, float]:
        return {
            "euro_hicp_index": 127.4,
            "euro_industrial_sentiment": -9.3,
            "euro_unemployment_rate": 6.4,
        }


# ---------------------------------------------------------------------------
# yfinance
# ---------------------------------------------------------------------------
class YFinanceAdapter(IngestionAdapter):
    config: YFinanceConfig

    def __init__(self, config: YFinanceConfig, fetch: AsyncFetch | None = None) -> None:
        super().__init__(config, fetch)

    async def _collect_once(self) -> dict[str, float]:
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError("yfinance not installed") from exc

        symbols = list(self.config.tickers.keys())

        def _download() -> dict[str, float]:
            data = yf.download(
                tickers=" ".join(symbols),
                period=self.config.lookback_period,
                interval=self.config.interval,
                progress=False,
                group_by="ticker",
                threads=True,
            )
            out: dict[str, float] = {}
            for ticker, feature in self.config.tickers.items():
                try:
                    close = data[ticker]["Close"].dropna()
                    if len(close):
                        out[feature] = float(close.iloc[-1])
                except (KeyError, IndexError):
                    continue
            return out

        # yfinance is blocking -> run it in a worker thread.
        out = await asyncio.to_thread(_download)
        if not out:
            raise RuntimeError("yfinance returned no usable data")
        return out

    def _mock(self) -> dict[str, float]:
        return {
            "gold_xau_spot": 2680.5, "silver_xag_spot": 31.4,
            "wti_crude_spot": 71.2, "brent_crude_spot": 75.9,
            "dollar_index_dxy": 104.3, "sp500_index": 5920.4,
            "nasdaq100_index": 21050.7, "hang_seng_index": 19780.0,
            "us10y_yield": 4.28,
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_adapter(config: SourceConfig, fetch: AsyncFetch | None = None) -> IngestionAdapter:
    """Instantiate the correct adapter for a source config."""
    mapping: dict[type[SourceConfig], type[IngestionAdapter]] = {
        FREDConfig: FREDAdapter,
        WorldBankConfig: WorldBankAdapter,
        IMFConfig: IMFAdapter,
        EurostatConfig: EurostatAdapter,
        YFinanceConfig: YFinanceAdapter,
    }
    adapter_cls = mapping.get(type(config))
    if adapter_cls is None:
        raise TypeError(f"no adapter registered for {type(config).__name__}")
    return adapter_cls(config, fetch)  # type: ignore[arg-type]
