"""ECONITH :: collectors.tradfi_assets.poller

Session-based traditional-finance poller.

Polls the keyless Yahoo Finance chart endpoint for reference macro-market
instruments (DXY, gold, S&P 500, crude oil) at an intraday cadence and appends
point-in-time snapshots into the partitioned raw lake
(``datasets/raw/tradfi/...``). Zero ML dependencies (httpx only).

TradFi markets have sessions (closed on weekends/holidays); the poller simply
records whatever the endpoint returns and never assumes continuous data, so a
stale/closed session is stored faithfully rather than fabricated.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from collectors.shared.persistence import SnapshotWriter
from collectors.shared.schemas import AssetClass, CrossAssetTick, validate_tick

logger = logging.getLogger("econith.collectors.tradfi")

__all__ = ["TradFiConfig", "TradFiPoller"]

# Canonical symbol -> Yahoo Finance ticker.
_YF_TICKERS: dict[str, str] = {
    "DXY": "DX-Y.NYB",
    "GOLD": "GC=F",
    "SPX500": "^GSPC",
    "OIL": "CL=F",
    "US10Y": "^TNX",
}

_YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"


@dataclass(slots=True)
class TradFiConfig:
    symbols: tuple[str, ...] = ("DXY", "GOLD", "SPX500", "OIL", "US10Y")
    poll_interval_s: float = 300.0            # 5-minute intraday cadence
    data_root: Path = Path("datasets/raw")
    request_timeout_s: float = 20.0


class TradFiPoller:
    """Polls tradfi references on cadence and appends snapshots."""

    def __init__(self, config: TradFiConfig | None = None) -> None:
        self._cfg = config or TradFiConfig()
        self._writer = SnapshotWriter(self._cfg.data_root, flush_threshold=1)
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="tradfi-poll")
        logger.info("tradfi poller started (symbols=%s)", self._cfg.symbols)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
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

    async def _loop(self) -> None:
        while self._running:
            await self._poll_once()
            try:
                await asyncio.sleep(self._cfg.poll_interval_s)
            except asyncio.CancelledError:
                raise

    async def _poll_once(self) -> None:
        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed -- cannot poll tradfi")
            return
        appended = 0
        headers = {"User-Agent": "Mozilla/5.0 (econith-collector)"}
        async with httpx.AsyncClient(timeout=self._cfg.request_timeout_s, headers=headers) as client:
            for symbol in self._cfg.symbols:
                snap = await self._fetch_one(client, symbol)
                if snap is None:
                    continue
                try:
                    self._writer.add(validate_tick(snap))
                    appended += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("tradfi %s snapshot dropped: %s", symbol, exc)
        if appended:
            await self._writer.flush()
            logger.info("tradfi snapshots appended: %d/%d", appended, len(self._cfg.symbols))

    async def _fetch_one(self, client, symbol: str):
        ticker = _YF_TICKERS.get(symbol.upper())
        if not ticker:
            return None
        try:
            resp = await client.get(_YF_CHART.format(ticker=ticker), params={"interval": "5m", "range": "1d"})
            resp.raise_for_status()
            result = resp.json()["chart"]["result"][0]
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            if price is None:
                return None
            return CrossAssetTick(
                ts_ms=int(time.time() * 1000),
                asset_class=AssetClass.TRADFI.value,
                symbol=symbol.upper(),
                channel="spot",
                source="yfinance",
                value=float(price),
                payload={
                    "ticker": ticker,
                    "price": float(price),
                    "currency": meta.get("currency"),
                    "prev_close": meta.get("chartPreviousClose"),
                },
            )
        except Exception as exc:  # noqa: BLE001 - one symbol failing must not abort the poll
            logger.debug("tradfi fetch %s failed: %s", symbol, exc)
            return None


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s"
    )
    asyncio.run(TradFiPoller().run_forever())


if __name__ == "__main__":
    main()
