"""Tải các lần công bố vĩ mô ban đầu của Mỹ và Nhật Bản từ ALFRED/FRED."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import pandas as pd

from KHKT_Evaluation.rq1_v2.fetch_fred_releases import (
    FredRequestError,
    normalize,
    request_series,
)
from KHKT_Evaluation.rq1_v3.run import configure_console


SERIES = {
    "US": {
        "interest_rate": ("FEDFUNDS", "percent_level", 0),
        "inflation": ("CPIAUCSL", "year_over_year", 12),
        "unemployment": ("UNRATE", "percent_level", 0),
        "gdp_growth": ("GDPC1", "annualized_quarter", 3),
    },
    "JP": {
        "interest_rate": ("IRSTCI01JPM156N", "percent_level", 0),
        "inflation": ("JPNCPIALLMINMEI", "year_over_year", 12),
        "unemployment": ("LRUN64TTJPM156S", "percent_level", 0),
        "gdp_growth": ("JPNRGDPEXP", "annualized_quarter", 3),
    },
}


def collect(api_key, start, end, *, pause=.55):
    rows = []
    calls = 0
    for country, features in SERIES.items():
        for feature, (series_id, transform, lookback) in features.items():
            if calls and pause:
                time.sleep(pause)
            request_start = (pd.Timestamp(start) - pd.DateOffset(months=lookback)).date().isoformat()
            observations = request_series(series_id, api_key, request_start, end)
            normalized = normalize(feature, series_id, transform, observations, start)
            for row in normalized:
                row.update(country=country, quality="observed")
            rows.extend(normalized)
            calls += 1
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise ValueError("Không có lần công bố nào được tải.")
    return frame.sort_values(["available_at", "country", "feature", "observation_at"])


def main():
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2008-01-01")
    parser.add_argument("--end", default=pd.Timestamp.now(tz="UTC").date().isoformat())
    parser.add_argument("--output", type=Path, default=Path("datasets/rq1_v3/releases.csv"))
    parser.add_argument("--api-key", default=os.getenv("FRED_API_KEY", ""))
    args = parser.parse_args()
    key = args.api_key.strip()
    if len(key) != 32 or not key.isalnum():
        parser.error("Cần FRED_API_KEY hợp lệ gồm 32 ký tự chữ và số.")
    if args.output.exists() or args.output.with_suffix(".metadata.json").exists():
        parser.error("Output hoặc metadata đã tồn tại; không ghi đè dữ liệu nguồn.")
    try:
        frame = collect(key, args.start, args.end)
    except (FredRequestError, ValueError) as exc:
        parser.exit(2, f"Không tải được dữ liệu vĩ mô: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    metadata = {
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "provider": "FRED/ALFRED series observations, output_type=4, units=lin",
        "availability_rule": "realtime_start plus one calendar day at 00:00 UTC",
        "countries": list(SERIES),
        "series": {country: {feature: spec[0] for feature, spec in values.items()}
                   for country, values in SERIES.items()},
        "start": args.start,
        "end": args.end,
        "rows": len(frame),
    }
    args.output.with_suffix(".metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Đã tạo {args.output} với {len(frame)} lần công bố ban đầu.")


if __name__ == "__main__":
    main()
