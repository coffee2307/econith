"""Tạo lịch dữ liệu ban đầu từ ALFRED/FRED cho RQ1 v2."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path

import pandas as pd


API = "https://api.stlouisfed.org/fred/series/observations"
SERIES = {
    "interest_rate": ("FEDFUNDS", "percent_level", 0),
    "inflation": ("CPIAUCSL", "year_over_year", 12),
    "unemployment": ("UNRATE", "percent_level", 0),
    "gdp_growth": ("GDPC1", "annualized_quarter", 3),
}


class FredRequestError(RuntimeError):
    """Lỗi FRED đã loại bỏ URL chứa khóa API."""


def request_series(series_id, api_key, start, end):
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "output_type": 4,
        # FRED chỉ chấp nhận units=lin khi output_type là 3 hoặc 4.
        "units": "lin",
        "observation_start": start,
        "observation_end": end,
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "limit": 100000,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ECONITH-RQ1/2"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response).get("observations", [])
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8", errors="replace"))
            detail = payload.get("error_message") or payload.get("message")
        except (ValueError, AttributeError):
            detail = None
        message = str(detail or exc.reason or "yêu cầu bị từ chối").strip()
        raise FredRequestError(f"FRED HTTP {exc.code}: {message}") from None
    except URLError as exc:
        raise FredRequestError(f"Không kết nối được FRED: {exc.reason}") from None


def _initial_observations(series_id, observations):
    records = []
    for item in observations:
        raw = item.get("value")
        if raw in (None, "."):
            continue
        released = pd.Timestamp(item["realtime_start"], tz="UTC")
        observed = pd.Timestamp(item["date"], tz="UTC")
        if released < observed:
            raise ValueError(f"{series_id}: ngày công bố trước kỳ quan sát {observed.date()}.")
        records.append((observed, released, float(raw)))
    if not records:
        raise ValueError(f"FRED không trả dữ liệu cho {series_id}.")

    # output_type=4 vốn chỉ trả lần công bố đầu tiên. Giữ bản sớm nhất ở đây để
    # dữ liệu vẫn an toàn nếu API trả trùng một kỳ quan sát.
    records.sort(key=lambda row: (row[0], row[1]))
    return {observed: (released, value) for observed, released, value in reversed(records)}


def _transform(value, previous, transform):
    if transform == "percent_level":
        return value / 100.0
    if previous is None or previous <= 0 or value <= 0:
        return None
    if transform == "year_over_year":
        return value / previous - 1.0
    if transform == "annualized_quarter":
        return (value / previous) ** 4 - 1.0
    raise ValueError(f"Phép biến đổi không được hỗ trợ: {transform}")


def normalize(feature, series_id, transform, observations, output_start=None):
    initial = _initial_observations(series_id, observations)
    start = pd.Timestamp(output_start, tz="UTC") if output_start else None
    lag_months = {"year_over_year": 12, "annualized_quarter": 3}.get(transform, 0)
    rows = []
    for observed, (released, raw) in sorted(initial.items()):
        if start is not None and observed < start:
            continue
        previous = None
        if lag_months:
            prior = observed - pd.DateOffset(months=lag_months)
            prior_release = initial.get(prior)
            if prior_release is None or prior_release[0] > released:
                continue
            previous = prior_release[1]
        value = _transform(raw, previous, transform)
        if value is None:
            continue
        # API chỉ cho ngày. Dùng từ 00:00 UTC ngày kế tiếp để không giả định giờ công bố.
        rows.append({
            "available_at": (released + pd.Timedelta(days=1)).isoformat(),
            "observation_at": observed.isoformat(),
            "feature": feature,
            "value": value,
            "unit": "fraction",
            "source": (
                f"FRED/ALFRED {series_id}; output_type=4; units=lin; "
                f"local_transform={transform}; initial release"
            ),
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
    for index, (feature, (series_id, transform, lookback_months)) in enumerate(SERIES.items()):
        if index:
            time.sleep(.55)  # thấp hơn giới hạn 120 yêu cầu/phút của FRED v1
        try:
            request_start = (
                pd.Timestamp(args.start) - pd.DateOffset(months=lookback_months)
            ).date().isoformat()
            observations = request_series(series_id, key, request_start, args.end)
        except FredRequestError as exc:
            parser.exit(2, f"Không tải được {series_id}: {exc}\n")
        rows.extend(normalize(feature, series_id, transform, observations, args.start))
    frame = pd.DataFrame(rows).sort_values(["available_at", "feature", "observation_at"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    metadata = {
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "endpoint": API,
        "output_type": 4,
        "availability_rule": "realtime_start plus one calendar day at 00:00 UTC",
        "series": {
            name: {"id": spec[0], "units": "lin", "local_transform": spec[1]}
            for name, spec in SERIES.items()
        },
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
