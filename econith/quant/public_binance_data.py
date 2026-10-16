"""Read-only Binance Spot hourly market snapshot for offline ECONITH research.

No API credentials, order, account, leverage or live execution endpoints.
Use only public exchangeInfo and klines. Missing data -> failure, not imputation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from econith.quant.paper_v1 import validated_candles

_BASE = "https://api.binance.com/api/v3"


def public_get(endpoint: str, params: dict, *, retries: int = 3) -> object:
    if endpoint not in {"exchangeInfo", "klines"}:
        raise ValueError("only public market-data endpoints supported")
    address = f"{_BASE}/{endpoint}?{urlencode(params)}"
    for attempt in range(retries):
        try:
            with urlopen(address, timeout=12) as response:
                if getattr(response, "status", 200) != 200:
                    raise RuntimeError("unexpected HTTP status")
                return json.loads(response.read(8_000_000).decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt + 1 == retries:
                raise RuntimeError("public market-data request failed") from exc
            time.sleep(min(2**attempt, 4))
    raise AssertionError("unreachable")


def _rules(exchange_info: dict, symbol: str) -> dict:
    listings = [item for item in exchange_info.get("symbols", []) if item.get("symbol") == symbol]
    if len(listings) != 1 or listings[0].get("status") != "TRADING":
        raise ValueError(f"{symbol}: not currently spot-trading")
    market = listings[0]
    if "SPOT" not in market.get("permissions", []) and not any(
        "SPOT" in group for group in market.get("permissionSets", [])
    ):
        raise ValueError(f"{symbol}: Spot permission not confirmed")
    filters = {f.get("filterType"): f for f in market.get("filters", [])}
    lot = filters.get("LOT_SIZE")
    notion = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
    if not lot or not notion:
        raise ValueError(f"{symbol}: missing order filters")
    step = str(lot["stepSize"])
    minimum = float(notion["minNotional"])
    if float(step) <= 0 or not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("invalid market filters")
    return {"step_size": step, "min_notional": minimum}


def capture_spot_hourly(symbols: list[str], *, limit: int = 300,
                        asof_ms: int | None = None, get=public_get,
                        interval: str = "1h") -> dict:
    """Capture contiguous fully-closed 1h or 1d spot candles in UTC."""
    if not symbols or len(symbols) > 8 or len(set(symbols)) != len(symbols):
        raise ValueError("select 1-8 unique symbols")
    if not 20 <= limit <= 1000:
        raise ValueError("limit must be in 20..1000")
    if interval not in ("1h", "4h", "1d"):
        raise ValueError("supported intervals: 1h, 4h, 1d")
    timeframe_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[interval]
    asof = int(time.time() * 1000) if asof_ms is None else int(asof_ms)
    snapshots: dict[str, list] = {}
    rulebook: dict[str, dict] = {}
    for symbol in symbols:
        if not symbol.isascii() or not symbol.isalnum() or symbol != symbol.upper():
            raise ValueError(f"invalid symbol: {symbol!r}")
        info = get("exchangeInfo", {"symbol": symbol})
        rulebook[symbol] = _rules(info, symbol)
        raw = get("klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not isinstance(raw, list):
            raise ValueError(f"{symbol}: malformed response")
        if any(not isinstance(row, list) or len(row) < 7 for row in raw):
            raise ValueError(f"{symbol}: malformed candle record")
        closed = [row for row in raw if int(row[6]) < asof]
        validated_candles(closed, timeframe_ms=timeframe_ms, asof_ms=asof)
        if asof - int(closed[-1][6]) > 3 * timeframe_ms:
            raise ValueError(f"{symbol}: stale market data")
        snapshots[symbol] = closed

    shared = set.intersection(*(set(int(r[0]) for r in rs) for rs in snapshots.values()))
    if len(shared) < 20:
        raise ValueError("too few aligned, fully closed candles")
    aligned = {}
    for symbol, rows in snapshots.items():
        aligned[symbol] = [row for row in rows if int(row[0]) in shared]
        validated_candles(aligned[symbol], timeframe_ms=timeframe_ms, asof_ms=asof)
    return {"source": "binance_spot_public", "asof_ms": asof,
            "symbols": sorted(symbols), "timeframe": interval, "market": aligned,
            "rules": rulebook}


def write_snapshot(result: dict, out_dir: Path) -> dict:
    """Atomic individual-file writes; manifest records hashes for verification."""
    out_dir.mkdir(parents=True, exist_ok=True)
    blobs = {
        "market.json": json.dumps(result["market"], separators=(",", ":")),
        "rules.json": json.dumps(result["rules"], separators=(",", ":")),
    }
    checksums = {}
    for filename, contents in blobs.items():
        data = contents.encode("utf-8")
        checksums[filename] = hashlib.sha256(data).hexdigest()
        temp = out_dir / (filename + ".partial")
        temp.write_bytes(data)
        os.replace(temp, out_dir / filename)
    manifest = {k: result[k] for k in ("source", "asof_ms", "symbols", "timeframe")}
    manifest["sha256"] = checksums
    temp = out_dir / "manifest.json.partial"
    temp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(temp, out_dir / "manifest.json")
    return manifest



def load_snapshot(out_dir: Path) -> dict:
    """Verify and load immutable snapshot files before replay."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("source") != "binance_spot_public" or manifest.get("timeframe") not in ("1h", "4h", "1d"):
        raise ValueError("unsupported snapshot provenance")
    if set(manifest.get("sha256", {})) != {"market.json", "rules.json"}:
        raise ValueError("incomplete snapshot manifest")
    contents = {}
    for filename in ("market.json", "rules.json"):
        binary = (out_dir / filename).read_bytes()
        if hashlib.sha256(binary).hexdigest() != manifest["sha256"][filename]:
            raise ValueError(f"snapshot checksum mismatch: {filename}")
        contents[filename] = json.loads(binary.decode("utf-8"))
    if set(contents["market.json"]) != set(contents["rules.json"]):
        raise ValueError("market/rules symbol mismatch")
    if sorted(contents["market.json"]) != manifest.get("symbols"):
        raise ValueError("manifest symbol mismatch")
    return {"market": contents["market.json"], "rules": contents["rules.json"],
            "asof_ms": int(manifest["asof_ms"]), "timeframe": manifest["timeframe"]}



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download public Spot 1h data, never place orders")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--interval", choices=("1h", "4h", "1d"), default="1h")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    snapshot = capture_spot_hourly(args.symbols, limit=args.limit, interval=args.interval)
    manifest = write_snapshot(snapshot, args.out_dir)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
