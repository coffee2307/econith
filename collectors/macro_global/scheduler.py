"""ECONITH :: collectors.macro_global.scheduler

Standalone, cron-like macro ingestion loop with strict point-in-time snapshot
persistence. Unlike the runtime ``core.ingestion.MacroIngestionHub`` (which
publishes onto the live event bus), this collector's ONLY job is to fetch and
append historical snapshots to disk for retrospective cross-asset training.

Zero ML dependencies — uses ``httpx`` for the network and the shared
:class:`SnapshotWriter` for storage. Each source polls on its own cadence.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from collectors.shared.persistence import SnapshotWriter
from collectors.shared.schemas import AssetClass, CrossAssetTick, validate_tick

logger = logging.getLogger("econith.collectors.macro_global")

__all__ = ["MacroSourceSpec", "MacroScheduler"]

# St. Louis Fed FRED series -> semantic feature name (keyed; needs FRED_API_KEY).
_FRED_SERIES: dict[str, str] = {
    "FEDFUNDS": "fed_funds_effective_rate",
    "CPIAUCSL": "consumer_price_index",
    "UNRATE": "unemployment_rate",
    "T10Y2Y": "yield_spread_10y_2y",
    "DGS10": "treasury_10y_yield",
    "DGS2": "treasury_2y_yield",
}


@dataclass(slots=True)
class MacroSourceSpec:
    """A single macro source and its poll cadence."""

    name: str
    poll_interval_s: float
    channel: str = "macro_series"


class MacroScheduler:
    """Polls macro sources on cadence and appends point-in-time snapshots."""

    def __init__(
        self,
        *,
        fred_api_key: str = "",
        data_root: Path | str = "datasets/raw",
        fred_interval_s: float = 6 * 3600.0,
        base_url: str = "https://api.stlouisfed.org/fred",
    ) -> None:
        self._fred_api_key = (fred_api_key or "").strip()
        self._base_url = base_url
        self._fred_interval = fred_interval_s
        self._writer = SnapshotWriter(data_root, flush_threshold=1)
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def has_fred(self) -> bool:
        v = self._fred_api_key.lower()
        return bool(v) and not v.startswith("your_") and "here" not in v

    async def start(self) -> None:
        self._running = True
        self._tasks = [asyncio.create_task(self._fred_loop(), name="macro-fred")]
        logger.info("macro_global scheduler started (fred=%s)", self.has_fred)

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._writer.close()

    async def run_forever(self) -> None:
        await self.start()
        try:
            while self._running:
                await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # -- FRED loop ------------------------------------------------------------
    async def _fred_loop(self) -> None:
        while self._running:
            await self._pull_fred()
            try:
                await asyncio.sleep(self._fred_interval)
            except asyncio.CancelledError:
                raise

    async def _pull_fred(self) -> None:
        if not self.has_fred:
            logger.info("FRED key absent -- skipping macro pull (set FRED_API_KEY)")
            return
        features = await self._fetch_fred_series()
        if not features:
            return
        tick = CrossAssetTick(
            ts_ms=int(time.time() * 1000),
            asset_class=AssetClass.MACRO.value,
            symbol="FRED",
            channel="macro_series",
            source="fred",
            value=features.get("fed_funds_effective_rate"),
            payload=features,
        )
        self._writer.add(validate_tick(tick))
        await self._writer.flush()
        logger.info("macro snapshot appended (%d series)", len(features))

    async def _fetch_fred_series(self) -> dict[str, float]:
        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed -- cannot fetch FRED")
            return {}
        out: dict[str, float] = {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            for series_id, feature in _FRED_SERIES.items():
                try:
                    resp = await client.get(
                        f"{self._base_url}/series/observations",
                        params={
                            "series_id": series_id,
                            "api_key": self._fred_api_key,
                            "file_type": "json",
                            "sort_order": "desc",
                            "limit": 1,
                        },
                    )
                    resp.raise_for_status()
                    obs = resp.json().get("observations", [])
                    if obs and obs[0].get("value") not in (".", None):
                        out[feature] = float(obs[0]["value"])
                except Exception as exc:  # noqa: BLE001 - one bad series must not abort the pull
                    logger.debug("FRED series %s skipped: %s", series_id, exc)
        return out


def main() -> None:
    import os

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s"
    )
    scheduler = MacroScheduler(fred_api_key=os.getenv("FRED_API_KEY", ""))
    asyncio.run(scheduler.run_forever())


if __name__ == "__main__":
    main()
