"""Tải giá điều chỉnh của nhiều nhóm tài sản và dựng lịch phiên để kiểm tra."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError

import numpy as np
import pandas as pd

from KHKT_Evaluation.rq1_v3.run import configure_console


YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
BINANCE = "https://api.binance.com/api/v3/klines"
ASSETS = {
    "SPY": ("equity_us_large", "yahoo", "SPY"),
    "QQQ": ("equity_us_technology", "yahoo", "QQQ"),
    "IWM": ("equity_us_small", "yahoo", "IWM"),
    "EWJ": ("equity_japan", "yahoo", "EWJ"),
    "TLT": ("bond_us_long_treasury", "yahoo", "TLT"),
    "LQD": ("bond_us_investment_grade", "yahoo", "LQD"),
    "GLD": ("gold", "yahoo", "GLD"),
    "USO": ("oil", "yahoo", "USO"),
    "UUP": ("usd", "yahoo", "UUP"),
    "BTCUSDT": ("crypto_bitcoin", "binance", "BTCUSDT"),
    "ETHUSDT": ("crypto_ethereum", "binance", "ETHUSDT"),
}


class MarketRequestError(RuntimeError):
    pass


def request_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "ECONITH-RQ1/3"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except HTTPError as exc:
        raise MarketRequestError(f"HTTP {exc.code}: {exc.reason}") from None
    except URLError as exc:
        raise MarketRequestError(f"Không kết nối được nguồn giá: {exc.reason}") from None


def yahoo_prices(symbol, start, end, requester=request_json):
    params = {
        "period1": int(pd.Timestamp(start, tz="UTC").timestamp()),
        "period2": int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp()),
        "interval": "1d",
        "events": "div,splits,capitalGains",
        "includeAdjustedClose": "true",
    }
    payload = requester(YAHOO.format(symbol=urllib.parse.quote(symbol)) + "?" + urllib.parse.urlencode(params))
    chart = payload.get("chart", {})
    if chart.get("error") or not chart.get("result"):
        raise MarketRequestError(f"Yahoo không trả dữ liệu cho {symbol}: {chart.get('error')}")
    result = chart["result"][0]
    stamps = result.get("timestamp", [])
    adjusted = ((result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or [])
    if len(stamps) != len(adjusted):
        raise MarketRequestError(f"Yahoo trả timestamp/adjusted close lệch nhau cho {symbol}.")
    rows = []
    for stamp, value in zip(stamps, adjusted):
        if value is None or not np.isfinite(value) or value <= 0:
            continue
        session = pd.Timestamp(stamp, unit="s", tz="UTC").tz_convert("America/New_York").date()
        rows.append((session, float(value)))
    if len(rows) < 100:
        raise MarketRequestError(f"{symbol} có ít hơn 100 phiên hợp lệ.")
    return rows


def nyse_rows(asset, prices, start, end):
    try:
        import pandas_market_calendars as mcal
    except ImportError as exc:
        raise MarketRequestError("Cần cài requirements-research.txt để kiểm tra lịch NYSE.") from exc
    calendar = mcal.get_calendar("NYSE")
    schedule = calendar.schedule(start_date=start, end_date=end)
    closes = {stamp.date(): close for stamp, close in schedule.market_close.items()}
    available = dict(prices)
    first, last = min(available), max(available)
    expected = {day: close for day, close in closes.items() if first <= day <= last}
    missing = sorted(set(expected) - set(available))
    extra = sorted(set(available) - set(expected))
    if missing or extra:
        raise MarketRequestError(
            f"{asset} không khớp lịch NYSE: thiếu {len(missing)}, ngoài lịch {len(extra)}."
        )
    market = [{"asset": asset, "time": expected[day].isoformat(), "adjusted_close": available[day]}
              for day in sorted(expected)]
    sessions = [{"asset": asset, "time": expected[day].isoformat()} for day in sorted(expected)]
    return market, sessions


def binance_prices(symbol, start, end, requester=request_json):
    cursor = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    finish = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp() * 1000) - 1
    rows = []
    while cursor <= finish:
        params = {"symbol": symbol, "interval": "1d", "startTime": cursor,
                  "endTime": finish, "limit": 1000}
        batch = requester(BINANCE + "?" + urllib.parse.urlencode(params))
        if isinstance(batch, dict):
            raise MarketRequestError(f"Binance từ chối {symbol}: {batch.get('msg', batch)}")
        if not batch:
            break
        for item in batch:
            close_time, close = int(item[6]), float(item[4])
            if np.isfinite(close) and close > 0:
                rows.append((pd.Timestamp(close_time, unit="ms", tz="UTC"), close))
        next_cursor = int(batch[-1][0]) + 86_400_000
        if next_cursor <= cursor:
            raise MarketRequestError(f"Binance không tiến được cursor cho {symbol}.")
        cursor = next_cursor
    if len(rows) < 100:
        raise MarketRequestError(f"{symbol} có ít hơn 100 ngày hợp lệ.")
    market = [{"asset": symbol, "time": stamp.isoformat(), "adjusted_close": close}
              for stamp, close in rows]
    sessions = [{"asset": symbol, "time": stamp.isoformat()} for stamp, _ in rows]
    return market, sessions


def collect(start, end, assets, *, pause=.2):
    market, sessions, metadata = [], [], {}
    for index, asset in enumerate(assets):
        if asset not in ASSETS:
            raise ValueError(f"Tài sản chưa khai báo: {asset}")
        group, provider, symbol = ASSETS[asset]
        if index and pause:
            time.sleep(pause)
        if provider == "yahoo":
            prices = yahoo_prices(symbol, start, end)
            rows, calendar = nyse_rows(asset, prices, start, end)
            source = "Yahoo Finance chart adjusted close; NYSE calendar verified separately"
        else:
            rows, calendar = binance_prices(symbol, start, end)
            source = "Binance public daily klines; continuous UTC calendar"
        market.extend(rows)
        sessions.extend(calendar)
        metadata[asset] = {"group": group, "provider": provider, "symbol": symbol,
                           "source": source, "rows": len(rows)}
    return (pd.DataFrame(market).sort_values(["asset", "time"]),
            pd.DataFrame(sessions).sort_values(["asset", "time"]), metadata)


def main():
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2008-01-01")
    parser.add_argument("--end", default=pd.Timestamp.now(tz="UTC").date().isoformat())
    parser.add_argument("--assets", nargs="+", default=list(ASSETS))
    parser.add_argument("--directory", type=Path, default=Path("datasets/rq1_v3"))
    args = parser.parse_args()
    paths = [args.directory / name for name in ("market.csv", "sessions.csv", "market.metadata.json")]
    if any(path.exists() for path in paths):
        parser.error("Một output đã tồn tại; không ghi đè dữ liệu nguồn.")
    try:
        market, sessions, assets = collect(args.start, args.end, args.assets)
    except (MarketRequestError, ValueError) as exc:
        parser.exit(2, f"Không tải được dữ liệu thị trường: {exc}\n")
    args.directory.mkdir(parents=True, exist_ok=True)
    market.to_csv(paths[0], index=False)
    sessions.to_csv(paths[1], index=False)
    paths[2].write_text(json.dumps({"created_at": pd.Timestamp.now(tz="UTC").isoformat(),
                                    "start": args.start, "end": args.end,
                                    "assets": assets}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã tạo {paths[0]} và {paths[1]} cho {len(assets)} tài sản.")


if __name__ == "__main__":
    main()
