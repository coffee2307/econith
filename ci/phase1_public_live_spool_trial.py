"""ONE manually authorized, finite public Spot WSS in-session spool test.

Experimental read-only SIMULATION. Requires trusted public clock preflight,
fresh output path, fixed 128 ingress, bounded source->publication lag. A
failure quarantines the entire source; no retries, no orders, no default-app
cutover or claims of continuous uninterrupted exchange market coverage.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from dataclasses import asdict
from pathlib import Path

from ci.phase1_public_spot_acceptance import measure_public_clock
from infrastructure.spot_forward_failure_diagnostics import safe_failure_code
from infrastructure.spot_forward_verified_research import observe_forward_spot_research


async def run(root:Path,*,frames:int,timeout_s:float,max_lag_ms:int,
              reuse_derived_connections:bool=False,
              diagnose_pressure:bool=False,
              reuse_producer_connections:bool=False,
              reuse_receiver_connection:bool=False,
              reuse_original_connection:bool=False,
              cooperative_reader_yield:bool=False,
              measure_checkpoint_completion:bool=False)->dict:
    await measure_public_clock(
        max_abs_offset_ms=3000,max_rtt_ms=2000,
    )
    result=await observe_forward_spot_research(
        output_dir=root,session_id="rec_live_spool_"+secrets.token_hex(8),
        allow_public_network=True,source="binance_public_simulation",
        max_frames=frames,timeout_s=timeout_s,queue_capacity=128,
        max_book_staleness_ms=2500,persistent_receiver=True,
        spooled_in_session=True,allow_experimental_public_spool=True,
        max_projector_lag_ms=max_lag_ms,max_pending_originals=512,
        max_source_raw_bytes=200_000_000,
        experiment_reuse_derived_connections=reuse_derived_connections,
        diagnose_pressure=diagnose_pressure,
        experiment_reuse_producer_connections=reuse_producer_connections,
        experiment_reuse_receiver_connection=reuse_receiver_connection,
        experiment_reuse_original_connection=reuse_original_connection,
        experiment_cooperative_reader_yield=cooperative_reader_yield,
        experiment_checkpoint_completion_timing=measure_checkpoint_completion,
    )
    if (not result.session_complete or not result.publication_in_session
        or not result.all_receipts_independent or result.orders_submitted
        or result.spool_original_cursor!=result.original_records
        or result.queue_high_watermark>=128
        or result.max_projector_lag_ms>max_lag_ms
        or result.projector_age_samples!=result.receiver_receipts
        or result.projector_p99_admission_age_upper_ms is None
        or result.projector_min_headroom_ms is None
        or result.projector_min_headroom_ms<0):
        raise RuntimeError("bounded public spool research proof incomplete")
    if measure_checkpoint_completion:
        from infrastructure.spot_checkpoint_delivery_probe import inspect as inspect_times
        timing = inspect_times(root)
        if (timing["source_to_after_checkpoint_samples"]!=result.receiver_receipts
            or timing["max_source_to_after_checkpoint_ms"]!=result.source_to_checkpoint_max_ms
            or result.source_to_checkpoint_samples!=result.receiver_receipts):
            raise RuntimeError("source-to-checkpoint forensic latency mismatch")
    else:
        timing = None
    return {
        **asdict(result),
        "verdict":"PASSED_FINITE_PUBLIC_IN_SESSION_SPOOL_RESEARCH_ONLY",
        "checkpoint_completion_independently_reaudited":timing is not None,
        "checkpoint_completion_p95_ms":(
            timing["p95_source_to_after_checkpoint_ms"] if timing else None),
        "checkpoint_completion_p99_ms":(
            timing["p99_source_to_after_checkpoint_ms"] if timing else None),
        "history_gap_free_verified":False,
        "default_market_ingress_cutover":False,
        "institutional_phase1_complete":False,
        "derived_connection_reuse_experimental":reuse_derived_connections,
        "pressure_diagnostic_opt_in":diagnose_pressure,
        "producer_connection_reuse_experimental":reuse_producer_connections,
        "receiver_connection_reuse_experimental":reuse_receiver_connection,
        "original_connection_reuse_experimental":reuse_original_connection,
        "cooperative_reader_yield_experimental":cooperative_reader_yield,
    }


def main(argv:list[str]|None=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-public-market-data",action="store_true")
    p.add_argument("--accept-experimental-spool",action="store_true")
    p.add_argument("--measure-checkpoint-completion",action="store_true",
                   help="source receipt through AFTER durable checkpoint, new local ledger audit")
    p.add_argument("--cooperative-reader-yield",action="store_true",
                   help="full-cache-only experimental reader event-loop fairness")
    p.add_argument("--reuse-original-connection",action="store_true",
                   help="research-only opt-in raw+intent WAL/FULL capture DB cache")
    p.add_argument("--reuse-receiver-connection",action="store_true",
                   help="research-only: keep receiver child SQLite WAL/FULL connection per session")
    p.add_argument("--reuse-producer-connections",action="store_true",
                   help="opt-in separately committed outbox/checkpoint cached SQLite producers")
    p.add_argument("--diagnose-pressure",action="store_true",
                   help="bounded source-vs-projector and stage profiling for investigation")
    p.add_argument("--reuse-derived-connections",action="store_true",
                   help="opt-in research-only observer connection reuse")
    p.add_argument("--output-dir",type=Path,required=True)
    p.add_argument("--frames",type=int,default=1000)
    p.add_argument("--timeout-s",type=float,default=120.0)
    p.add_argument("--max-lag-ms",type=int,default=5000)
    args=p.parse_args(argv)
    if (not args.allow_public_market_data
        or not args.accept_experimental_spool
        or (args.measure_checkpoint_completion and not args.accept_experimental_spool)
        or (args.cooperative_reader_yield and not (
            args.reuse_original_connection and args.reuse_receiver_connection
            and args.reuse_producer_connections and args.reuse_derived_connections))
        or (args.reuse_original_connection and not (
            args.reuse_receiver_connection
            and args.reuse_producer_connections
            and args.reuse_derived_connections))
        or (args.reuse_receiver_connection and not (
            args.reuse_producer_connections and args.reuse_derived_connections))
        or (args.reuse_producer_connections and not args.reuse_derived_connections)
        or not args.output_dir.is_absolute()
        or args.output_dir.exists()
        or args.output_dir!=args.output_dir.resolve()
        or type(args.frames) is not int or not 10<=args.frames<=1000
        or type(args.timeout_s) is not float or not 5<=args.timeout_s<=120
        or type(args.max_lag_ms) is not int or not 100<=args.max_lag_ms<=5000):
        p.error("refusing unsafe experimental public spool trial")
    try:
        result=asyncio.run(run(args.output_dir,frames=args.frames,
                               timeout_s=args.timeout_s,
                               max_lag_ms=args.max_lag_ms,
                               reuse_derived_connections=args.reuse_derived_connections,
                               diagnose_pressure=args.diagnose_pressure,
                               reuse_producer_connections=args.reuse_producer_connections,
                               reuse_receiver_connection=args.reuse_receiver_connection,
                               reuse_original_connection=args.reuse_original_connection,
                               cooperative_reader_yield=args.cooperative_reader_yield,
                               measure_checkpoint_completion=args.measure_checkpoint_completion))
    except Exception as exc:
        print(json.dumps({
            "verdict":"FAILED_OR_QUARANTINED_SOURCE",
            "failure_code":safe_failure_code(exc),
            "execution_started":False,"orders_submitted":0,
            "source_history_continuity_verified":False,
        },sort_keys=True),file=sys.stderr)
        return 2
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
