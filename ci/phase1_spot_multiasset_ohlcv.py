"""Read-only opt-in Binance Spot REST OHLCV bounded preflight; never trading evidence.

Original HTTP response bytes are stored in a fresh *local* research evidence root;
only aggregate reports are appropriate for public CI artifacts. Never 24h/continuity proof.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import ssl
import sys
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = "https://data-api.binance.vision"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT")
INTERVALS = {"1m": 60_000, "5m": 300_000}
ROWS = 100
MAX_HTTP_BYTES = 2_000_000
SCHEMA = "econith-phase1-multiasset-ohlcv-finite-v1"


def strict_int(value, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}: nonnegative integer required")
    return value


def strict_decimal(value, name: str) -> Decimal:
    if type(value) is not str or not value or len(value) > 60:
        raise ValueError(f"{name}: decimal string required")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{name}: invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{name}: nonnegative finite decimal required")
    return result


def audit_klines(raw: bytes, *, interval: str, cutoff_ms: int, expected_rows=ROWS) -> dict:
    if interval not in INTERVALS or len(raw) > MAX_HTTP_BYTES:
        raise ValueError("invalid interval or oversized source")
    step = INTERVALS[interval]
    if type(cutoff_ms) is not int or cutoff_ms < step:
        raise ValueError("invalid source as-of cutoff")
    rows = json.loads(raw)
    if type(rows) is not list or len(rows) != expected_rows:
        raise ValueError("bounded candle count mismatch")
    previous = None
    first = last = None
    for r in rows:
        if type(r) is not list or len(r) != 12:
            raise ValueError("unexpected Spot kline schema")
        opened, closed = strict_int(r[0], "open time"), strict_int(r[6], "close time")
        if opened % step or closed != opened + step - 1:
            raise ValueError("misaligned candle interval")
        if closed > cutoff_ms:
            raise ValueError("candle not closed at source as-of time")
        if previous is not None and opened != previous + step:
            raise ValueError("duplicate, inverted or missing kline time: no interpolation")
        op, hi, lo, cl = (strict_decimal(r[k], f"price[{k}]") for k in (1,2,3,4))
        base, quote, taker_base, taker_quote = (strict_decimal(r[k], f"volume[{k}]") for k in (5,7,9,10))
        count = strict_int(r[8], "number of trades")
        if lo <= 0 or not (lo <= op <= hi and lo <= cl <= hi):
            raise ValueError("OHLC price geometry invalid")
        if taker_base > base or taker_quote > quote:
            raise ValueError("taker volume exceeds total volume")
        if count == 0 and (base != 0 or quote != 0 or taker_base != 0 or taker_quote != 0):
            raise ValueError("zero-trade row has nonzero volume")
        if type(r[11]) is not str:
            raise ValueError("unexpected ignore field representation")
        first = opened if first is None else first
        last, previous = opened, opened
    if last + step - 1 != cutoff_ms:
        raise ValueError("latest requested closed candle missing: tail coverage UNKNOWN")
    return {"candles":len(rows),"first_open_ms":first,"last_open_ms":last,
            "last_close_ms":last+step-1,"time_gaps":0,"duplicate_opens":0,
            "closed_as_of_source_cutoff":True}


def request_public(path: str, params: dict | None = None, *, timeout_s: float = 12) -> bytes:
    if path not in ("/api/v3/time", "/api/v3/exchangeInfo", "/api/v3/klines",
                     "/api/v3/ticker/bookTicker"):
        raise ValueError("read-only public endpoint allowlist")
    url = BASE_URL + path + (("?"+urlencode(params)) if params else "")
    req = Request(url, headers={"Accept":"application/json","User-Agent":"ECONITH-Phase1-bounded-research/1.0"}, method="GET")
    with urlopen(req,timeout=timeout_s,context=ssl.create_default_context()) as response:
        if response.status != 200:
            raise ValueError("public HTTP response refused")
        data = response.read(MAX_HTTP_BYTES+1)
    if len(data) > MAX_HTTP_BYTES:
        raise ValueError("public HTTP body too large")
    return data


def write_original(root: Path, name: str, raw: bytes, registry: dict) -> None:
    if not raw or len(raw)>MAX_HTTP_BYTES or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_." for ch in name):
        raise ValueError("invalid original evidence input")
    target=root/name
    with target.open("xb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    registry[name] = {"sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)}


def prepare_root(root: Path) -> None:
    if not root.is_absolute() or root.exists() or root != root.resolve() or root.parent.is_symlink():
        raise ValueError("new absolute non-aliased evidence directory required")
    root.mkdir(mode=0o700,parents=False,exist_ok=False)


def market_snapshot(raw: bytes, symbol: str) -> dict:
    obj = json.loads(raw)
    if type(obj) is not dict or type(obj.get("symbols")) is not list or len(obj["symbols"]) != 1:
        raise ValueError("invalid exchangeInfo response")
    entry=obj["symbols"][0]
    if type(entry) is not dict or entry.get("symbol") != symbol:
        raise ValueError("symbol identity mismatch")
    if entry.get("status") != "TRADING":
        raise ValueError("symbol not currently TRADING; historical status not inferred")
    if entry.get("quoteAsset") != "USDT":
        raise ValueError("unexpected quote asset")
    return {"status_at_capture":entry["status"],"base_asset":entry.get("baseAsset"),
            "quote_asset":entry["quoteAsset"],"onboard_date_unverified":entry.get("onboardDate")}


def source_time(raw: bytes) -> int:
    obj=json.loads(raw)
    if type(obj) is not dict:
        raise ValueError("invalid Binance clock response")
    return strict_int(obj.get("serverTime"),"serverTime")


def collect(root: Path, *, requester=request_public) -> dict:
    prepare_root(root)
    original_registry = {}
    summary = {"schema":SCHEMA,"status":"STARTED_NOT_CERTIFIED", "mode":"SIMULATION",
               "source":"Binance public data-only REST", "originals":original_registry,
               "assets":[],"symbol_count":0,"candle_count":0,"continuously_observed":False,
               "history_continuity_verified":False,"venue_completeness_verified":False,
               "historical_listing_delisting_verified":False,
               "hmac_signatures_cryptographically_reverified":False,
               "default_runtime_cutover":False,"institutional_phase1_complete":False,
               "orders_submitted":0}
    try:
        initial_raw=requester("/api/v3/time")
        write_original(root,"server_before.json",initial_raw,original_registry)
        start_ms=source_time(initial_raw)
        for symbol in SYMBOLS:
            exchange_raw=requester("/api/v3/exchangeInfo",{"symbol":symbol})
            write_original(root,f"exchange_{symbol.lower()}.json",exchange_raw,original_registry)
            market=market_snapshot(exchange_raw,symbol)
            pair={"symbol":symbol,"market_snapshot":market,"intervals":{}}
            for interval,step in INTERVALS.items():
                cutoff=(start_ms//step)*step-1
                params={"symbol":symbol,"interval":interval,"limit":ROWS,"endTime":cutoff}
                raw=requester("/api/v3/klines",params)
                write_original(root,f"klines_{symbol.lower()}_{interval}.json",raw,original_registry)
                measured=audit_klines(raw,interval=interval,cutoff_ms=cutoff)
                pair["intervals"][interval]={**measured,"cutoff_ms":cutoff}
                summary["candle_count"]+=measured["candles"]
            summary["assets"].append(pair)
        final_raw=requester("/api/v3/time")
        write_original(root,"server_after.json",final_raw,original_registry)
        end_ms=source_time(final_raw)
        if end_ms < start_ms or end_ms - start_ms > 120_000:
            raise ValueError("Binance clock moved backward or bounded probe too slow")
        summary["symbol_count"]=len(summary["assets"])
        summary["source_time_before_ms"]=start_ms
        summary["source_time_after_ms"]=end_ms
        summary["status"]="PASSED_FINITE_REST_QUALITY_ONLY"
    except Exception as exc:
        summary["status"]="REFUSED_OR_INCOMPLETE"
        summary["failure_class"] = type(exc).__name__
    report = root/"aggregate_report.json"
    with report.open("x",encoding="utf-8") as f:
        json.dump(summary,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
        f.flush();os.fsync(f.fileno())
    return summary


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--acknowledge-finite-not-coverage",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args(argv)
    if not args.allow_public_market_data or not args.acknowledge_finite_not_coverage:
        p.error("explicit public-data and finite-only consent required")
    try:
        report=collect(args.output_dir)
    except (OSError,ValueError) as exc:
        print(json.dumps({"status":"REFUSED","failure_class":type(exc).__name__,"orders_submitted":0}))
        return 2
    print(json.dumps({k:v for k,v in report.items() if k!="originals"},sort_keys=True))
    return 0 if report["status"]=="PASSED_FINITE_REST_QUALITY_ONLY" else 2


if __name__ == "__main__":
    sys.exit(main())
