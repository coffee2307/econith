"""Deterministic-structure offline Spot burst probe, zero exchange I/O/orders.

Usage: python -m ci.phase1_offline_burst_probe --output-dir ABSENT_ABSOLUTE_DIR
       --frames 240 --spacing-ms 3
Synthetic frames are generated at receive time and are NOT claimed as
exchange-provided evidence. Every output directory is new and isolated.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from infrastructure.spot_bounded_durable_spool import capture_then_replay_sealed_spool
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code, failure_queue_peak


def _now() -> int:
    return time.time_ns() // 1_000_000


def _frame(data: dict) -> bytes:
    suffix = ("aggTrade" if data["e"] == "aggTrade" else "depth@100ms")
    return json.dumps({"stream": f"btcusdt@{suffix}", "data": data},
                      separators=(",", ":")).encode("utf-8")


async def _snapshot(_symbol: str, _limit: int) -> bytes:
    await asyncio.sleep(0)
    return json.dumps({
        "lastUpdateId": 100,
        "bids": [["100", "3"]], "asks": [["102", "5"]],
    }).encode("utf-8")


async def _source(frames: int, spacing_ms: float):
    now = _now()
    yield _frame({
        "e": "aggTrade", "s": "BTCUSDT", "E": now, "T": now - 10,
        "a": 900, "f": 900, "l": 900, "p": "101.25",
        "q": "0.2", "m": False,
    })
    now = _now()
    yield _frame({
        "e": "depthUpdate", "s": "BTCUSDT", "E": now,
        "U": 100, "u": 102, "b": [["100", "4"]], "a": [],
    })
    # Allow independent original COMMIT then REST snapshot sync.
    await asyncio.sleep(0.20)
    book_id = 102
    trade_id = 900
    for index in range(2, frames):
        now = _now()
        if index % 3 == 0:
            trade_id += 1
            yield _frame({
                "e": "aggTrade", "s": "BTCUSDT", "E": now,
                "T": now - 10, "a": trade_id, "f": trade_id,
                "l": trade_id, "p": "101.25", "q": "0.2", "m": False,
            })
        else:
            book_id += 1
            yield _frame({
                "e": "depthUpdate", "s": "BTCUSDT", "E": now,
                "U": book_id, "u": book_id,
                "b": [["100", "4"]], "a": [],
            })
        if spacing_ms:
            await asyncio.sleep(spacing_ms / 1000)
        else:
            await asyncio.sleep(0)


async def probe(root: Path, *, frames: int, spacing_ms: float,
                sealed_spool: bool = False) -> int:
    old = os.environ.get("ECONITH_PHASE1_STAGE_PROFILE")
    os.environ["ECONITH_PHASE1_STAGE_PROFILE"] = "1"
    started = time.monotonic()
    try:
        try:
            if sealed_spool:
                status = await capture_then_replay_sealed_spool(
                    output_dir=root, session_id="rec_offline_burst_probe",
                    stream=_source(frames, spacing_ms),
                    snapshot_fetch=_snapshot, max_frames=frames,
                    timeout_s=120,
                )
            else:
                status = await observe_forward_spot_research(
                    output_dir=root, session_id="rec_offline_burst_probe",
                    stream=_source(frames, spacing_ms),
                    snapshot_fetch=_snapshot, max_frames=frames,
                    timeout_s=120, persistent_receiver=True,
                    max_book_staleness_ms=2500,
                )
        except Exception as exc:
            # No raw payloads, paths or child secrets in outputs.
            result={"result":"QUARANTINED", "failure_class":type(exc).__name__,
                    "failure_code":safe_failure_code(exc),
                    "queue_peak_on_failure":failure_queue_peak(exc),
                    "synthetic_only":True,"orders_submitted":0}
            code=2
        else:
            result={"result":("SEALED_SPOOL_OFFLINE_PASS" if sealed_spool
                              else "BOUNDED_OFFLINE_PASS"),
                    "originals":(status.originals if sealed_spool else status.original_records),
                    "receiver_receipts":(status.independent_receiver_receipts
                                         if sealed_spool else status.receiver_receipts),
                    "queue_peak":(status.capture_queue_high_watermark
                                  if sealed_spool else status.queue_high_watermark),
                    "sealed_only_not_live":sealed_spool,
                    "synthetic_only":True,"orders_submitted":0}
            if sealed_spool:
                result.update({
                    "capture_elapsed_s":status.capture_elapsed_s,
                    "sealed_replay_elapsed_s":status.replay_elapsed_s,
                    "original_raw_bytes":status.raw_source_bytes,
                    "checkpoint_count":status.checkpoints,
                })
            code=0
    finally:
        if old is None:
            os.environ.pop("ECONITH_PHASE1_STAGE_PROFILE",None)
        else:
            os.environ["ECONITH_PHASE1_STAGE_PROFILE"]=old
    result["elapsed_s"]=round(time.monotonic()-started,2)
    profiler=root/"stage_latency.json"
    if profiler.is_file():
        result["stage_latency"]=json.loads(profiler.read_text(encoding="utf-8"))
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return code


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--frames",type=int,default=240)
    p.add_argument("--spacing-ms",type=float,default=3.0)
    p.add_argument("--sealed-spool",action="store_true",
                   help="Source-first append then validated sealed research replay, NOT live")
    a=p.parse_args()
    if (not a.output_dir.is_absolute() or a.output_dir.exists()
        or type(a.frames) is not int or not 10<=a.frames<=1000
        or not 0<=a.spacing_ms<=200):
        p.error("refusing non-fresh or unsafe bounded offline configuration")
    return asyncio.run(probe(a.output_dir,frames=a.frames,
                             spacing_ms=a.spacing_ms,
                             sealed_spool=a.sealed_spool))


if __name__=="__main__":
    raise SystemExit(main())
