"""Independent read-only integrity/quality re-audit of private finite Spot originals."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from ci.phase1_spot_multiasset_ohlcv import (
    SYMBOLS, INTERVALS, ROWS, SCHEMA, audit_klines, market_snapshot, source_time,
    MAX_HTTP_BYTES,
)


def inspect(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir() or root != root.resolve():
        raise ValueError("invalid isolated research root")
    report_path=root/"aggregate_report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ValueError("aggregate report unavailable")
    report=json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema")!=SCHEMA:
        raise ValueError("unexpected evidence report schema")
    registry=report.get("originals")
    names={"server_before.json","server_after.json"}
    for sym in SYMBOLS:
        names.add(f"exchange_{sym.lower()}.json")
        for interval in INTERVALS:
            names.add(f"klines_{sym.lower()}_{interval}.json")
    if type(registry) is not dict or set(registry)!=names:
        raise ValueError("missing or excess original source evidence")
    raws={}
    for name in names:
        p=root/name
        if not p.is_file() or p.is_symlink() or p.stat().st_size>MAX_HTTP_BYTES:
            raise ValueError("missing, oversized or symlinked original")
        raw=p.read_bytes()
        ref=registry[name]
        if (type(ref) is not dict or ref.get("bytes")!=len(raw)
            or ref.get("sha256")!=hashlib.sha256(raw).hexdigest()):
            raise ValueError("original bytes SHA mismatch")
        raws[name]=raw
    before,after=source_time(raws["server_before.json"]),source_time(raws["server_after.json"])
    if (after<before or after-before>120_000 or report.get("source_time_before_ms")!=before
        or report.get("source_time_after_ms")!=after):
        raise ValueError("source clock mismatch")
    inspected=[]
    for sym in SYMBOLS:
        market=market_snapshot(raws[f"exchange_{sym.lower()}.json"],sym)
        summary_entry=next((x for x in report.get("assets",[]) if x.get("symbol")==sym),None)
        if summary_entry is None or summary_entry.get("market_snapshot")!=market:
            raise ValueError("exchange symbol evidence mismatch")
        for interval,step in INTERVALS.items():
            cutoff=(before//step)*step-1
            quality=audit_klines(raws[f"klines_{sym.lower()}_{interval}.json"],interval=interval,cutoff_ms=cutoff)
            if summary_entry.get("intervals",{}).get(interval)!={**quality,"cutoff_ms":cutoff}:
                raise ValueError("original/aggregate consistency mismatch")
            inspected.append({"symbol":sym,"interval":interval,"candles":quality["candles"]})
    accepted=(
        report.get("status")=="PASSED_FINITE_REST_QUALITY_ONLY"
        and report.get("symbol_count")==len(SYMBOLS)
        and len(report.get("assets",[]))==len(SYMBOLS)
        and report.get("candle_count")==len(SYMBOLS)*len(INTERVALS)*ROWS
        and report.get("mode")=="SIMULATION"
        and report.get("continuously_observed") is False
        and report.get("history_continuity_verified") is False
        and report.get("venue_completeness_verified") is False
        and report.get("historical_listing_delisting_verified") is False
        and report.get("default_runtime_cutover") is False
        and report.get("institutional_phase1_complete") is False
        and report.get("orders_submitted")==0
    )
    return {"schema":"econith-phase1-multiasset-ohlcv-independent-audit-v1",
            "verdict":"PASSED_FINITE_REST_ONLY" if accepted else "NOT_ACCEPTED",
            "checked_markets":len(inspected),"total_candles":sum(x["candles"] for x in inspected),
            "independent_source_retrieval":False,"binance_history_completeness":False,
            "historical_listing_delisting_verified":False,"orders_submitted":0}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    args=p.parse_args(argv)
    try:result=inspect(args.root)
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
        result={"verdict":"AUDIT_REFUSED","failure_class":type(exc).__name__,"orders_submitted":0}
    print(json.dumps(result,sort_keys=True))
    return 0 if result["verdict"]=="PASSED_FINITE_REST_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
