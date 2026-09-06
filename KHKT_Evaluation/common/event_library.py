# -*- coding: utf-8 -*-
"""Historical event windows for EXP_006 (real calendar dates within dataset span)."""
from __future__ import annotations

from datetime import datetime, timezone

# Dataset BTC features span ~2022-04-15 → 2026-07-25 (UTC).
# Only include events overlapping that span. Labels are documentary; windows are real.

EVENTS: list[dict] = [
    {
        "id": "fed_hike_cycle_2022h2",
        "name": "Fed aggressive hike cycle (2022 H2)",
        "kind": "rate_hike",
        "start": "2022-06-01",
        "end": "2022-12-31",
        "notes": "Rapid policy-rate increases; risk-off regime in risk assets.",
    },
    {
        "id": "svb_banking_stress_2023q1",
        "name": "US regional banking stress (SVB window)",
        "kind": "growth_crash",
        "start": "2023-03-01",
        "end": "2023-03-31",
        "notes": "SVB / regional bank stress March 2023.",
    },
    {
        "id": "fed_peak_hold_2023h2",
        "name": "Peak-rate / restrictive policy (2023 H2)",
        "kind": "rate_hike",
        "start": "2023-07-01",
        "end": "2023-12-31",
        "notes": "Policy rates near cycle highs; restrictive stance.",
    },
    {
        "id": "yen_carry_vol_2024aug",
        "name": "August 2024 risk-off / FX volatility episode",
        "kind": "inflation_spike",
        "start": "2024-08-01",
        "end": "2024-08-15",
        "notes": "Global risk-off episode early August 2024 (FX/vol).",
    },
]


def to_ts_ms(date_str: str, *, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return int(dt.timestamp() * 1000)


def event_windows() -> list[dict]:
    out = []
    for e in EVENTS:
        row = dict(e)
        row["start_ts_ms"] = to_ts_ms(e["start"])
        row["end_ts_ms"] = to_ts_ms(e["end"], end_of_day=True)
        out.append(row)
    return out


def split_pre_during_post(
    start: str,
    end: str,
    *,
    pre_days: int = 14,
    post_days: int = 14,
) -> list[dict]:
    """Calendar pre / during / post windows around an event [start, end]."""
    from datetime import timedelta

    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    pre_start = start_dt - timedelta(days=int(pre_days))
    pre_end = start_dt - timedelta(seconds=1)
    post_start = end_dt + timedelta(days=1)
    post_end = end_dt + timedelta(days=int(post_days))
    post_end = post_end.replace(hour=23, minute=59, second=59)
    end_eod = end_dt.replace(hour=23, minute=59, second=59)
    return [
        {
            "phase": "pre",
            "start_ts_ms": int(pre_start.timestamp() * 1000),
            "end_ts_ms": int(pre_end.timestamp() * 1000),
        },
        {
            "phase": "during",
            "start_ts_ms": int(start_dt.timestamp() * 1000),
            "end_ts_ms": int(end_eod.timestamp() * 1000),
        },
        {
            "phase": "post",
            "start_ts_ms": int(post_start.timestamp() * 1000),
            "end_ts_ms": int(post_end.timestamp() * 1000),
        },
    ]