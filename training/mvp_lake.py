"""ECONITH :: training.mvp_lake — mount VPS HF lake + long-horizon Binance/macro.

Use this after downloading ``datasets.tar`` from the collector VPS.

Important
---------
* Do **not** try to download 5 years of tick-by-tick / aggTrades. That is
  multi-terabyte and useless for MVP. Use **1h + 1d klines** for history.
* VPS tape (~Jul 2026) is the HF micro window (depth / trades). Keep it.
* Combine by putting everything under ``datasets/raw/{market,macro,tradfi}``
  then run ``python -m training.quant.feature_pipeline``.

Examples
--------
    # 1) Extract VPS tar into the lake (run once; ~50GB, slow on HDD)
    mkdir F:\\econith\\datasets\\raw
    tar -xf F:\\econith-coin-data\\datasets.tar -C F:\\econith\\datasets\\raw

    # 2) Backfill 5y klines (public, no key) into the same lake
    python -m training.mvp_lake backfill-klines \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT,DOGEUS,AVAXUSDT \\
        --intervals 1h,1d --start 2021-01-01 --end 2026-07-26

    # 3) Macro + TradFi history
    python -m training.mvp_lake backfill-fred --start 2015-01-01
    python -m training.mvp_lake backfill-tradfi --start 2021-01-01

    # 4) Glue + label (1h bars => use swing horizons)
    python -m training.quant.feature_pipeline --raw-root datasets/raw --out-dir datasets/features
    python -m training.quant.label_symbol --input datasets/features --horizon-preset swing_1h
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.mvp_lake")

PUBLIC_DATA_HOST = "https://api.binance.com"
FUTURES_DATA_HOST = "https://fapi.binance.com"
_KLINE_COLS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_base",
    "taker_buy_quote",
    "ignore",
)

_FRED_SERIES: dict[str, str] = {
    "FEDFUNDS": "fed_funds_effective_rate",
    "CPIAUCSL": "consumer_price_index",
    "CPILFESL": "core_cpi",
    "PCEPILFE": "core_pce",
    "UNRATE": "unemployment_rate",
    "U6RATE": "unemployment_u6",
    "ICSA": "initial_jobless_claims",
    "T10Y2Y": "yield_spread_10y_2y",
    "T10YIE": "breakeven_inflation_10y",
    "T5YIE": "breakeven_inflation_5y",
    "INDPRO": "industrial_production",
    "TCU": "capacity_utilization_total",
    "DGS10": "treasury_10y_yield",
    "DGS2": "treasury_2y_yield",
    "DGS30": "treasury_30y_yield",
    "DGS3MO": "treasury_3m_yield",
    "DFF": "fed_funds_daily",
    "SOFR": "sofr",
    "VIXCLS": "vix",
    "DTWEXBGS": "usd_broad_index",
    "DEXUSEU": "usd_eur",
    "DEXCHUS": "cny_usd",
    "DEXJPUS": "jpy_usd",
    "BAMLH0A0HYM2": "hy_oas_spread",
    "BAMLC0A0CM": "ig_oas_spread",
    "TEDRATE": "ted_spread",
    "WALCL": "fed_balance_sheet",
    "WTREGEN": "treasury_general_account",
    "RRPONTSYD": "overnight_rrp",
    "M2SL": "m2_money_supply",
    "BOGMBASE": "monetary_base",
    "UMCSENT": "consumer_sentiment",
    "HOUST": "housing_starts",
    "PERMIT": "building_permits",
    "CSUSHPISA": "case_shiller_home",
    "RSAFS": "retail_sales",
    "DGORDER": "durable_goods_orders",
    "PAYEMS": "nonfarm_payrolls",
    "AWHAETP": "avg_weekly_hours",
    "CES0500000003": "avg_hourly_earnings",
    "PPIACO": "ppi_all_commodities",
    "PCEPI": "pce_price_index",
    "GDP": "nominal_gdp",
    "GDPC1": "real_gdp",
    "A191RL1Q225SBEA": "real_gdp_growth",
}

# World Bank indicator code -> semantic name (keyless).
_WB_INDICATORS: dict[str, str] = {
    "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct",
    "NY.GDP.PCAP.KD.ZG": "gdp_per_capita_growth",
    "FP.CPI.TOTL.ZG": "inflation_cpi_pct",
    "SL.UEM.TOTL.ZS": "unemployment_ilo",
    "GC.DOD.TOTL.GD.ZS": "debt_to_gdp",
    "NE.RSB.GNFS.ZS": "trade_balance_pct_gdp",
    "BN.CAB.XOKA.GD.ZS": "current_account_pct_gdp",
    "FI.RES.TOTL.CD": "foreign_reserves_usd",
    "BX.KLT.DINV.WD.GD.ZS": "fdi_pct_gdp",
    "FM.LBL.BMNY.GD.ZS": "broad_money_pct_gdp",
}
_WB_COUNTRIES: tuple[str, ...] = (
    "USA",
    "CHN",
    "JPN",
    "DEU",
    "VNM",
    "GBR",
    "IND",
    "EUU",
)

_YF_TICKERS: dict[str, str] = {
    "DXY": "DX-Y.NYB",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "SPX500": "^GSPC",
    "NASDAQ": "^IXIC",
    "OIL": "CL=F",
    "NATGAS": "NG=F",
    "COPPER": "HG=F",
    "US10Y": "^TNX",
    "VIX": "^VIX",
    "EURUSD": "EURUSD=X",
    "USDJPY": "JPY=X",
    "USDCNH": "CNH=X",
    "GBPUSD": "GBPUSD=X",
}

_DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "LTCUSDT",
)


def _to_millis(date_str: str) -> int:
    s = date_str.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue
    raise SystemExit(f"unrecognised date '{date_str}' (use YYYY-MM-DD)")


def cmd_status(raw_root: Path) -> int:
    market = raw_root / "market"
    macro = raw_root / "macro"
    tradfi = raw_root / "tradfi"
    print(f"raw_root = {raw_root.resolve()}")
    for name, path in (("market", market), ("macro", macro), ("tradfi", tradfi)):
        if not path.exists():
            print(f"  [{name}] MISSING")
            continue
        n = sum(1 for _ in path.rglob("*.parquet"))
        print(f"  [{name}] {n} parquet shards under {path}")
    tar = Path("F:/econith-coin-data/datasets.tar")
    if tar.exists():
        print(f"  VPS tar present: {tar} ({tar.stat().st_size / 1e9:.1f} GB)")
    else:
        print("  VPS tar not found at F:/econith-coin-data/datasets.tar")
    print(
        "\nExtract hint (one-time):\n"
        "  mkdir F:\\econith\\datasets\\raw\n"
        "  tar -xf F:\\econith-coin-data\\datasets.tar -C F:\\econith\\datasets\\raw\n"
        "  # expects paths like datasets/raw/market/crypto_high_beta/..."
    )
    return 0


def _write_ticks(path: Path, rows: list[dict[str, Any]]) -> None:
    import pandas as pd

    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_parquet(path, engine="pyarrow", compression="snappy", index=False)
    logger.info("wrote %d rows -> %s", len(df), path)


def _fetch_klines(
    client: Any, symbol: str, interval: str, start_ms: int, end_ms: int
) -> list[list[Any]]:
    out: list[list[Any]] = []
    cursor = start_ms
    while cursor < end_ms:
        resp = client.get(
            "/api/v3/klines",
            params={
                "symbol": symbol.upper(),
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        out.extend(batch)
        cursor = int(batch[-1][0]) + 1
        if len(batch) < 1000:
            break
        time.sleep(0.05)  # be polite to the public endpoint
    return out


def cmd_backfill_klines(
    *,
    symbols: list[str],
    intervals: list[str],
    start: str,
    end: str,
    raw_root: Path,
    base_url: str,
) -> int:
    """Download OHLCV and store as CrossAssetTick rows under datasets/raw/market."""
    import httpx

    start_ms = _to_millis(start)
    end_ms = _to_millis(end)
    total = 0
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        for symbol in symbols:
            for interval in intervals:
                rows_raw = _fetch_klines(client, symbol, interval, start_ms, end_ms)
                if not rows_raw:
                    logger.warning("no klines for %s %s", symbol, interval)
                    continue
                ticks: list[dict[str, Any]] = []
                for row in rows_raw:
                    mapped = dict(zip(_KLINE_COLS, row))
                    open_time = int(mapped["open_time"])
                    close = float(mapped["close"])
                    payload = {
                        "open": float(mapped["open"]),
                        "high": float(mapped["high"]),
                        "low": float(mapped["low"]),
                        "close": close,
                        "volume": float(mapped["volume"]),
                        "trades": int(mapped["trades"]),
                        "interval": interval,
                        "p": close,
                        "c": close,
                    }
                    ticks.append(
                        {
                            "ts_ms": open_time,
                            "asset_class": "market",
                            "symbol": symbol.upper(),
                            "channel": f"kline_{interval}",
                            "source": "binance_klines",
                            "value": close,
                            "payload": json.dumps(payload, separators=(",", ":")),
                        }
                    )
                out = (
                    raw_root
                    / "market"
                    / "klines_hist"
                    / symbol.upper()
                    / f"{symbol.upper()}_{interval}.parquet"
                )
                _write_ticks(out, ticks)
                total += len(ticks)
    logger.info("klines backfill done: %d rows", total)
    return 0 if total else 1


def cmd_backfill_fred(*, start: str, raw_root: Path, api_key: str) -> int:
    import httpx
    import os

    key = (api_key or os.getenv("FRED_API_KEY") or "").strip()
    if not key or key.startswith("your_"):
        raise SystemExit(
            "FRED_API_KEY required (free at https://fred.stlouisfed.org/). "
            "Pass --api-key or set env FRED_API_KEY."
        )

    out_rows: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0) as client:
        for series_id, name in _FRED_SERIES.items():
            try:
                resp = client.get(
                    "https://api.stlouisfed.org/fred/series/observations",
                    params={
                        "series_id": series_id,
                        "api_key": key,
                        "file_type": "json",
                        "observation_start": start,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("FRED %s HTTP %s — skip", series_id, resp.status_code)
                    continue
                obs = resp.json().get("observations") or []
            except Exception as exc:  # noqa: BLE001
                logger.warning("FRED %s failed (%s) — skip", series_id, exc)
                continue
            kept = 0
            for item in obs:
                val = item.get("value")
                if val in (None, "."):
                    continue
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                day = str(item.get("date") or "")
                try:
                    ts_ms = _to_millis(day)
                except SystemExit:
                    continue
                out_rows.append(
                    {
                        "ts_ms": ts_ms,
                        "asset_class": "macro",
                        "symbol": series_id,
                        "channel": name,
                        "source": "fred",
                        "value": num,
                        "payload": json.dumps(
                            {"series_id": series_id, "name": name, "date": day},
                            separators=(",", ":"),
                        ),
                    }
                )
                kept += 1
            logger.info("FRED %s: %d usable / %d raw", series_id, kept, len(obs))
            time.sleep(0.15)

    if not out_rows:
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m")
    path = raw_root / "macro" / "fred" / stamp / "fred_history__00000.parquet"
    _write_ticks(path, out_rows)
    return 0


def cmd_backfill_world_bank(*, start_year: int, raw_root: Path) -> int:
    """Pull World Bank annual/structural series for tracked countries (keyless)."""
    import httpx

    out_rows: list[dict[str, Any]] = []
    countries = ";".join(_WB_COUNTRIES)
    end_year = datetime.now(timezone.utc).year
    with httpx.Client(timeout=60.0) as client:
        for indicator, name in _WB_INDICATORS.items():
            url = (
                f"https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"
            )
            try:
                resp = client.get(
                    url,
                    params={
                        "format": "json",
                        "per_page": 20000,
                        "date": f"{start_year}:{end_year}",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("WB %s HTTP %s — skip", indicator, resp.status_code)
                    continue
                body = resp.json()
                rows = body[1] if isinstance(body, list) and len(body) > 1 else []
            except Exception as exc:  # noqa: BLE001
                logger.warning("WB %s failed (%s) — skip", indicator, exc)
                continue
            kept = 0
            for item in rows or []:
                val = item.get("value")
                if val is None:
                    continue
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                year = str(item.get("date") or "")
                iso3 = ((item.get("countryiso3code") or item.get("country", {}).get("id") or "")
                        if isinstance(item.get("country"), dict)
                        else (item.get("countryiso3code") or ""))
                if not iso3:
                    iso3 = str((item.get("country") or {}).get("id") or "UNK")
                try:
                    ts_ms = _to_millis(f"{year}-01-01")
                except SystemExit:
                    continue
                out_rows.append(
                    {
                        "ts_ms": ts_ms,
                        "asset_class": "macro",
                        "symbol": f"{iso3}_{indicator}",
                        "channel": f"wb_{name}",
                        "source": "world_bank",
                        "value": num,
                        "payload": json.dumps(
                            {
                                "indicator": indicator,
                                "name": name,
                                "country": iso3,
                                "year": year,
                            },
                            separators=(",", ":"),
                        ),
                    }
                )
                kept += 1
            logger.info("WB %s: %d points", indicator, kept)
            time.sleep(0.2)

    if not out_rows:
        return 1
    stamp = datetime.now(timezone.utc).strftime("%Y%m")
    path = raw_root / "macro" / "world_bank" / stamp / "wb_history__00000.parquet"
    _write_ticks(path, out_rows)
    return 0


def cmd_backfill_tradfi(*, start: str, end: str, raw_root: Path) -> int:
    """One-shot Yahoo daily history for DXY/gold/oil/SPX/... into tradfi lake."""
    import httpx

    start_s = _to_millis(start) // 1000
    end_s = _to_millis(end) // 1000
    total = 0
    with httpx.Client(timeout=30.0, headers={"User-Agent": "econith-mvp-lake/1.0"}) as client:
        for sym, yf in _YF_TICKERS.items():
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf}"
            resp = client.get(
                url,
                params={
                    "period1": start_s,
                    "period2": end_s,
                    "interval": "1d",
                    "events": "history",
                },
            )
            if resp.status_code != 200:
                logger.warning("yahoo %s (%s) HTTP %s", sym, yf, resp.status_code)
                continue
            body = resp.json()
            result = (body.get("chart") or {}).get("result") or []
            if not result:
                logger.warning("yahoo %s empty", sym)
                continue
            r0 = result[0]
            ts_list = r0.get("timestamp") or []
            quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
            closes = quote.get("close") or []
            rows: list[dict[str, Any]] = []
            for ts, close in zip(ts_list, closes):
                if close is None:
                    continue
                rows.append(
                    {
                        "ts_ms": int(ts) * 1000,
                        "asset_class": "tradfi",
                        "symbol": sym,
                        "channel": "spot_1d",
                        "source": "yfinance",
                        "value": float(close),
                        "payload": json.dumps(
                            {"price": float(close), "c": float(close), "yf": yf},
                            separators=(",", ":"),
                        ),
                    }
                )
            if not rows:
                continue
            path = raw_root / "tradfi" / "history" / sym / f"{sym}_1d.parquet"
            _write_ticks(path, rows)
            total += len(rows)
            time.sleep(0.3)
    logger.info("tradfi backfill done: %d rows", total)
    return 0 if total else 1


def cmd_backfill_futures_alt(
    *,
    symbols: list[str],
    start: str,
    end: str,
    raw_root: Path,
    base_url: str = FUTURES_DATA_HOST,
    oi_period: str = "1h",
) -> int:
    """Pull funding + open-interest history into ``alt/futures/`` for HF merge.

    Funding: full history via ``/fapi/v1/fundingRate`` (paginated).
    Open interest: Binance only keeps ~30 days via ``/futures/data/openInterestHist``.
    Liquidation notional is left at 0 (no stable public long history API).
    """
    import httpx

    start_ms = _to_millis(start)
    end_ms = _to_millis(end)
    out_dir = raw_root / "alt" / "futures"
    total = 0

    with httpx.Client(timeout=30.0, headers={"User-Agent": "econith-mvp-lake/1.0"}) as client:
        for sym in symbols:
            # ---- funding rate (paginated, ascending) ----
            funding_rows: list[dict[str, Any]] = []
            cursor = start_ms
            while cursor < end_ms:
                resp = client.get(
                    f"{base_url}/fapi/v1/fundingRate",
                    params={
                        "symbol": sym,
                        "startTime": cursor,
                        "endTime": end_ms,
                        "limit": 1000,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("funding %s HTTP %s: %s", sym, resp.status_code, resp.text[:200])
                    break
                batch = resp.json() or []
                if not batch:
                    break
                for item in batch:
                    ft = int(item.get("fundingTime") or 0)
                    if ft <= 0:
                        continue
                    funding_rows.append(
                        {
                            "ts_ms": ft,
                            "symbol": sym,
                            "channel": "funding_rate",
                            "source": "binance_fapi",
                            "funding_rate": float(item.get("fundingRate") or 0.0),
                            "open_interest": 0.0,
                            "oi_change_pct": 0.0,
                            "liquidation_notional": 0.0,
                            "value": float(item.get("fundingRate") or 0.0),
                            "payload": json.dumps(item, separators=(",", ":")),
                        }
                    )
                last_t = int(batch[-1].get("fundingTime") or cursor)
                next_cursor = last_t + 1
                if next_cursor <= cursor or len(batch) < 1000:
                    break
                cursor = next_cursor
                time.sleep(0.15)

            # ---- open interest hist (last ~30d only) ----
            oi_rows: list[dict[str, Any]] = []
            oi_cursor = max(start_ms, end_ms - 30 * 86_400_000)
            while oi_cursor < end_ms:
                resp = client.get(
                    f"{base_url}/futures/data/openInterestHist",
                    params={
                        "symbol": sym,
                        "period": oi_period,
                        "startTime": oi_cursor,
                        "endTime": end_ms,
                        "limit": 500,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("oi %s HTTP %s: %s", sym, resp.status_code, resp.text[:200])
                    break
                batch = resp.json() or []
                if not batch:
                    break
                prev_oi: float | None = None
                for item in batch:
                    ts = int(item.get("timestamp") or 0)
                    oi = float(item.get("sumOpenInterest") or 0.0)
                    chg = 0.0 if prev_oi in (None, 0.0) else (oi - prev_oi) / max(prev_oi, 1e-12)
                    prev_oi = oi
                    oi_rows.append(
                        {
                            "ts_ms": ts,
                            "symbol": sym,
                            "channel": "open_interest",
                            "source": "binance_fapi",
                            "funding_rate": 0.0,
                            "open_interest": oi,
                            "oi_change_pct": float(chg),
                            "liquidation_notional": 0.0,
                            "value": oi,
                            "payload": json.dumps(item, separators=(",", ":")),
                        }
                    )
                last_t = int(batch[-1].get("timestamp") or oi_cursor)
                next_cursor = last_t + 1
                if next_cursor <= oi_cursor or len(batch) < 500:
                    break
                oi_cursor = next_cursor
                time.sleep(0.2)

            # Merge on ts: prefer combining funding + nearest OI via separate files.
            if funding_rows:
                path = out_dir / sym / "funding_history.parquet"
                _write_ticks(path, funding_rows)
                total += len(funding_rows)
            if oi_rows:
                path = out_dir / sym / "oi_history.parquet"
                _write_ticks(path, oi_rows)
                total += len(oi_rows)
            logger.info(
                "%s futures alt: funding=%d oi=%d",
                sym,
                len(funding_rows),
                len(oi_rows),
            )

    logger.info("futures alt backfill done: %d rows", total)
    return 0 if total else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mvp_lake.py", description="MVP raw-lake builder")
    p.add_argument(
        "--raw-root",
        default=str(_ROOT / "datasets" / "raw"),
        help="lake root (default: <repo>/datasets/raw)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="show what is already under the lake + extract hint")

    k = sub.add_parser("backfill-klines", help="Binance 1h/1d history as lake ticks")
    k.add_argument(
        "--symbols",
        default=",".join(_DEFAULT_SYMBOLS),
        help="comma-separated pairs",
    )
    k.add_argument("--intervals", default="1h,1d")
    k.add_argument("--start", default="2021-01-01")
    k.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    k.add_argument("--base-url", default=PUBLIC_DATA_HOST)

    f = sub.add_parser("backfill-fred", help="FRED multi-year series into macro lake")
    f.add_argument("--start", default="2015-01-01")
    f.add_argument("--api-key", default="")

    t = sub.add_parser("backfill-tradfi", help="Yahoo daily DXY/gold/oil/SPX into tradfi lake")
    t.add_argument("--start", default="2021-01-01")
    t.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    a = sub.add_parser(
        "backfill-futures-alt",
        help="Binance funding + OI history into alt/futures (HF merge)",
    )
    a.add_argument("--symbols", default=",".join(_DEFAULT_SYMBOLS[:4]))
    a.add_argument("--start", default="2024-01-01")
    a.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    a.add_argument("--base-url", default=FUTURES_DATA_HOST)
    a.add_argument("--oi-period", default="1h", choices=["5m", "15m", "30m", "1h", "4h", "1d"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_root = Path(args.raw_root)
    if args.cmd == "status":
        return cmd_status(raw_root)
    if args.cmd == "backfill-klines":
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
        return cmd_backfill_klines(
            symbols=symbols,
            intervals=intervals,
            start=args.start,
            end=args.end,
            raw_root=raw_root,
            base_url=args.base_url,
        )
    if args.cmd == "backfill-fred":
        return cmd_backfill_fred(start=args.start, raw_root=raw_root, api_key=args.api_key)
    if args.cmd == "backfill-tradfi":
        return cmd_backfill_tradfi(start=args.start, end=args.end, raw_root=raw_root)
    if args.cmd == "backfill-futures-alt":
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return cmd_backfill_futures_alt(
            symbols=symbols,
            start=args.start,
            end=args.end,
            raw_root=raw_root,
            base_url=args.base_url,
            oi_period=args.oi_period,
        )
    raise SystemExit(f"unknown command {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
