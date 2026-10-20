"""Independent aggregate-only public co-running Spot/HMAC pilot quality audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from ci.phase1_rotating_hmac_audit import inspect


def inspect_public(root:Path)->dict:
    proof=inspect(root,co_run=True)  # full original+receiver+3 observer SHA audit
    if proof.get("verdict")!="PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY":
        raise ValueError("source downstream forensic proof refused")
    source=json.loads((root/"aggregate_report.json").read_text(encoding="utf-8"))
    co=json.loads((root/"hmac_corun_aggregate.json").read_text(encoding="utf-8"))
    hist=source.get("original_commit_age_ms_histogram")
    if not isinstance(hist,dict) or set(hist)!={"0_100","101_500","501_1000","1001_5000"}:
        raise ValueError("missing audited original age distribution")
    if not all(type(x) is int and x>=0 for x in hist.values()):
        raise ValueError("invalid source latency bins")
    if not (
        source.get("schema")=="econith-phase1-single-reader-rotating-originals-v1"
        and source.get("verdict")=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY"
        and co.get("verdict")=="PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY"
        and co.get("public_read_only_market_source") is True
        and source.get("source")=="binance_public_data_only"
        and source.get("mode")==co.get("mode")=="SIMULATION"
        and source.get("source_socket_count")==co.get("source_socket_count_observed")==1
        and source.get("source_reader_task_count")==1
        and type(source.get("target_duration_s")) in (int,float)
        and 90<=source["target_duration_s"]<=150
        and source.get("duration_target_reached") is True
        and source.get("duration_stream_elapsed_s",0)>=source["target_duration_s"]-0.05
        and type(source.get("segment_frame_limit")) is int
        and 250<=source["segment_frame_limit"]<=650
        and source.get("committed_ws_frames")==source.get("produced_ws_frames")==co.get("source_ws_frames")==source["committed_ws_frames"]
        and source.get("source_queue_capacity")==64
        and 1<=source.get("source_queue_high_watermark",65)<64
        and source.get("adapter_queue_capacity")==128
        and source.get("event_freshness_cap_ms")==5000
        and type(source.get("original_commit_age_ms_max")) is int
        and 0<=source["original_commit_age_ms_max"]<=5000
        and sum(hist.values())==proof["raw_original_events"]
        and source.get("journal_disk_bytes_peak",200_000_000)<=150_000_000
        and co.get("segment_handoff_age_budget_ms")==25000
        and 0<co.get("segment_handoff_lag_max_ms",999999)<=25000
        and 1<=co.get("segment_backlog_peak",3)<=2
        and co.get("concurrent_segments_completed",0)>=1
        and co.get("forward_checkpoints")==co.get("independent_receiver_receipts")==proof["forward_checkpoints"]
        and co.get("source_reader_attached_during_hmac") is True
        and co.get("per_segment_postseal_delivery") is True
        and co.get("full_continuous_market_hmac_certified") is False
        and source.get("exchange_history_completeness_verified") is False
        and co.get("unobserved_venue_history_certified") is False
        and co.get("default_ingress_cutover") is False
        and co.get("institutional_phase1_complete") is False
        and co.get("orders_submitted")==source.get("orders_submitted")==0
        and type(co.get("process_peak_rss_kib_linux")) is int
        and 0<co["process_peak_rss_kib_linux"]<1_000_000
    ):
        raise ValueError("bounded public HMAC source and/or resource policy not satisfied")
    return {
        "schema":"econith-phase1-public-corun-hmac-independent-audit-v1",
        "verdict":"PASSED_BOUNDED_PUBLIC_SPOT_CORUN_HMAC_RESEARCH_ONLY",
        "public_tls_source_assertion":"pinned factory selected by research runner",
        "source_sockets":1,
        "duration_s":source["duration_stream_elapsed_s"],
        "source_frames":source["committed_ws_frames"],
        "source_journals":proof["segments"],
        "snapshots":sum(s["snapshot_captures"] for s in source["segments"]),
        "source_originals":proof["raw_original_events"],
        "source_original_commit_age_ms_max":source["original_commit_age_ms_max"],
        "source_original_commit_age_histogram":hist,
        "hmac_projections":proof["source_projections"],
        "receiver_owned_ack":proof["receiver_owned_receipts"],
        "forward_checkpoints":proof["forward_checkpoints"],
        "segment_handoff_lag_max_ms":co["segment_handoff_lag_max_ms"],
        "segment_backlog_peak":co["segment_backlog_peak"],
        "upstream_fifo_peak":source["source_queue_high_watermark"],
        "journal_disk_peak_bytes":source["journal_disk_bytes_peak"],
        "process_peak_rss_kib_linux":co["process_peak_rss_kib_linux"],
        "independent_original_checkpoint_observer_sha_replay":True,
        "independent_hmac_wire_reauthentication":False,
        "exchange_venue_completeness_verified":False,
        "continuous_per_frame_hmac_verified":False,
        "orders_submitted":0,
    }

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    a=p.parse_args(argv)
    try:result=inspect_public(a.root)
    except (Exception,) as exc:
        result={"verdict":"AUDIT_REFUSED","failure_class":type(exc).__name__,
                "orders_submitted":0}
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0 if result["verdict"]=="PASSED_BOUNDED_PUBLIC_SPOT_CORUN_HMAC_RESEARCH_ONLY" else 2

if __name__=="__main__":
    sys.exit(main())
