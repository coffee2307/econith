"""Tạo lịch dữ liệu ban đầu từ ALFRED/FRED cho RQ1 v2."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd


API = "https://api.stlouisfed.org/fred/series/observations"
SERIES = {
    "interest_rate": ("FEDFUNDS", "lin"),
    "inflation": ("CPIAUCSL", "pc1"),
    "unemployment": ("UNRATE", "lin"),
    "gdp_growth": ("GDPC1", "pca"),
}


def request_series(series_id, units, api_key, start, end):
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "output_type": 4,
        "units": units,
        "observation_start": start,
        "observation_end": end,
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "limit": 100000,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ECONITH-RQ1/2"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response).get("observations", [])


def normalize(feature, series_id, units, observations):
    rows = []
    for item in observations:
        raw = item.get("value")
        if raw in (None, "."):
            continue
        released = pd.Timestamp(item["realtime_start"], tz="UTC")
        observed = pd.Timestamp(item["date"], tz="UTC")
        if released < observed:
            raise ValueError(f"{series_id}: ngày công bố trước kỳ quan sát {observed.date()}.")
        # API chỉ cho ngày. Dùng từ 00:00 UTC ngày kế tiếp để không giả định giờ công bố.
        rows.append({
            "available_at": (released + pd.Timedelta(days=1)).isoformat(),
            "observation_at": observed.isoformat(),
            "feature": feature,
            "value": float(raw) / 100.0,
            "unit": "fraction",
            "source": f"FRED/ALFRED {series_id}; output_type=4; units={units}; initial release",
        })
    if not rows:
        raise ValueError(f"FRED không trả dữ liệu cho {series_id}.")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2019-01-01")
    parser.add_argument("--end", default=pd.Timestamp.now(tz="UTC").date().isoformat())
    parser.add_argument("--output", type=Path, default=Path("datasets/macro_releases.csv"))
    parser.add_argument("--api-key", default=os.getenv("FRED_API_KEY", ""))
    args = parser.parse_args()
    key = args.api_key.strip()
    if len(key) != 32 or not key.isalnum():
        parser.error("Cần FRED_API_KEY hợp lệ gồm 32 ký tự chữ và số.")
    if args.output.exists():
        parser.error("Tệp output đã tồn tại; không ghi đè dữ liệu nguồn.")
    rows = []
    for index, (feature, (series_id, units)) in enumerate(SERIES.items()):
        if index:
            time.sleep(.55)  # thấp hơn giới hạn 120 yêu cầu/phút của FRED v1
        rows.extend(normalize(feature, series_id, units,
                              request_series(series_id, units, key, args.start, args.end)))
    frame = pd.DataFrame(rows).sort_values(["available_at", "feature", "observation_at"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    metadata = {
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "endpoint": API,
        "output_type": 4,
        "availability_rule": "realtime_start plus one calendar day at 00:00 UTC",
        "series": {name: {"id": spec[0], "units": spec[1]} for name, spec in SERIES.items()},
        "rows": len(frame),
        "start": args.start,
        "end": args.end,
    }
    args.output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Đã tạo {args.output} với {len(frame)} lần công bố ban đầu.")


if __name__ == "__main__":
    main()
