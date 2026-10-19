"""Read-only, aggregate-only 250ms ingress FIFO forensics.

No source identity or raw original, no HMAC secret and no continuity claim.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def inspect(path: Path) -> dict:
    if (path.is_symlink() or not path.is_file() or
        path.stat().st_size>512_000):
        raise ValueError("invalid bounded microburst diagnostic path")
    raw=json.loads(path.read_text(encoding="utf-8"))
    windows=raw.get("subsecond_windows")
    totals=raw.get("subsecond_fifo_totals")
    if (raw.get("schema")!="econith-phase1-spool-pressure-v1"
        or raw.get("diagnostic_only_not_source_evidence") is not True
        or raw.get("subsecond_window_ms")!=250
        or not isinstance(windows,list) or len(windows)>960
        or not isinstance(totals,dict)
        or any(type(totals.get(k)) is not int or totals[k]<0 for k in
               ("enqueued","dequeued","writer_completed","overflow"))):
        raise ValueError("unrecognized or unbounded diagnostic schema")
    keys=("elapsed_start_ms","enqueued","dequeued","writer_completed",
          "overflow","peak_fifo_depth","near_full_observations",
          "max_writer_service_ms")
    for window in windows:
        if (not isinstance(window,dict)
            or any(type(window.get(k)) is not int or window[k]<0
                   for k in keys)):
            raise ValueError("invalid microburst histogram window")
    overflow_windows=[i for i,w in enumerate(windows) if w["overflow"]>0]
    leading=[]
    if overflow_windows:
        first=overflow_windows[0]
        leading=windows[max(0,first-4):min(len(windows),first+2)]
    top=sorted(windows,
               key=lambda w:(w["peak_fifo_depth"],
                             w["enqueued"]-w["dequeued"],
                             w["max_writer_service_ms"]),reverse=True)[:6]
    return {
        "schema":"econith-phase1-subsecond-forensic-summary-v1",
        "source_complete":raw.get("source_complete") is True,
        "diagnostic_only_not_source_evidence":True,
        "window_ms":250,
        "available_windows":len(windows),
        "windows_truncated":raw.get("subsecond_windows_truncated") is True,
        "totals":{k:totals[k] for k in
                  ("enqueued","dequeued","writer_completed","overflow")},
        "max_fifo_depth":max((w["peak_fifo_depth"] for w in windows),default=0),
        "max_writer_service_ms":max((w["max_writer_service_ms"]
                                     for w in windows),default=0),
        "first_overflow_window_start_ms":(
            windows[overflow_windows[0]]["elapsed_start_ms"]
            if overflow_windows else None
        ),
        "pre_overflow_context":leading,
        "top_fifo_windows":top,
        "history_continuity_certified":False,
        "hmac_cryptography_reverified":False,
        "orders_submitted":0,
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--output",type=Path)
    a=p.parse_args(argv)
    try:
        result=inspect(a.input)
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({"result":"DIAGNOSTIC_REFUSED",
                          "error_class":type(exc).__name__,
                          "history_continuity_certified":False,
                          "orders_submitted":0}))
        return 2
    serialized=json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n"
    if a.output is not None:
        if a.output.exists() or a.output.is_symlink() or not a.output.parent.is_dir():
            return 2
        a.output.write_text(serialized,encoding="utf-8")
    print(serialized,flush=True)
    # Incomplete source remains incomplete even when forensic inspection succeeds.
    return 0


if __name__=="__main__":
    raise SystemExit(main())
