"""Independent read-only original SQLite audit for a single continuous-socket pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

def inspect(root:Path)->dict:
    if root.is_symlink() or not root.is_dir() or root!=root.resolve():
        raise ValueError("invalid evidence root")
    report_file=root/"aggregate_report.json"
    db_file=root/"original.sqlite"
    if not report_file.is_file() or report_file.is_symlink() or db_file.is_symlink() or not db_file.is_file():
        raise ValueError("missing or aliased evidence")
    owner_marker=root/".forward_producer.lock"
    if owner_marker.is_symlink():
        raise ValueError("unsafe owner marker link")
    orphan_marker_present=owner_marker.exists()
    report=json.loads(report_file.read_text(encoding="utf-8"))
    if report.get("schema")!="econith-phase1-single-socket-duration-v1":
        raise ValueError("wrong audit source schema")
    with sqlite3.connect(f"file:{db_file}?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0]!="ok":
            raise ValueError("SQLite check failed")
        sessions=db.execute("SELECT * FROM offline_spot_capture_sessions").fetchall()
        if len(sessions)!=1:
            raise ValueError("one source session per single-socket proof required")
        session=sessions[0]
        ids=db.execute("SELECT capture_id,session_id,kind,received_at_ms,raw_blob,sha256 FROM offline_spot_capture_records ORDER BY capture_id").fetchall()
        intents=db.execute("SELECT capture_id,session_id,kind,received_at_ms,sha256 FROM offline_forward_capture_intents ORDER BY capture_id").fetchall()
    if not ids or len(ids)!=len(intents):
        raise ValueError("captured originals/intents missing")
    counts={"aggTrade":0,"depthUpdate":0,"snapshot":0}
    prior=0
    for rec, intent in zip(ids,intents):
        if (rec["capture_id"]<=prior or rec["capture_id"]!=intent["capture_id"]
            or rec["session_id"]!=session["session_id"]
            or rec["session_id"]!=intent["session_id"]
            or rec["kind"]!=intent["kind"]
            or rec["received_at_ms"]!=intent["received_at_ms"]
            or rec["sha256"]!=intent["sha256"]
            or hashlib.sha256(rec["raw_blob"]).hexdigest()!=rec["sha256"]):
            raise ValueError("source SHA/intent mismatch or corrupt original")
        prior=rec["capture_id"]
        if rec["kind"] not in counts:
            raise ValueError("unexpected source record type")
        counts[rec["kind"]]+=1
    frames=counts["aggTrade"]+counts["depthUpdate"]
    accepted=(
        report.get("verdict")=="PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY"
        and report.get("continuous_source_connection_observed") is True
        and report.get("duration_target_reached") is True
        and report.get("mode")=="SIMULATION"
        and report.get("history_continuity_verified") is False
        and report.get("exchange_history_completeness_certified") is False
        and report.get("institutional_phase1_complete") is False
        and report.get("default_runtime_cutover") is False
        and report.get("orders_submitted")==0
        and type(report.get("target_duration_s")) in (int,float)
        and report.get("duration_stream_elapsed_s",0)>=report["target_duration_s"]-0.05
        and report.get("websocket_frames")==frames
        and counts["snapshot"]==report.get("snapshot_captures")==1
        and 2<=frames<=4998
        and report.get("max_fifo_depth",128)<128
        and report.get("queue_capacity")==128
        and report.get("freshness_cap_ms")==5000
        and report.get("depth_status")=="SYNCED"
        and report.get("exclusive_os_owner_fence_held") is True
        and report.get("max_terminal_source_silence_s")==5.0
        and type(report.get("terminal_source_silence_s")) in (float,int)
        and 0<=report["terminal_source_silence_s"]<=5.0
        and not orphan_marker_present
        and session["symbol"]=="BTCUSDT"
        and session["source"]=="binance_public_simulation"
        and session["closed_at_ms"] is not None
        and session["last_receipt_ms"]<=session["closed_at_ms"]
    )
    return {
        "schema":"econith-phase1-single-socket-duration-independent-audit-v1",
        "verdict":"PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY" if accepted else "NOT_ACCEPTED",
        "source_sessions":1,
        "orphan_owner_marker_present":orphan_marker_present,
        "originals":len(ids),"capture_intents":len(intents),
        "frames":frames,"snapshots":counts["snapshot"],
        "original_digest_mismatches":0,
        "single_source_duration_target_s":report.get("target_duration_s"),
        "observed_websocket_duration_s":report.get("duration_stream_elapsed_s"),
        "independently_reverified_exchange_completeness":False,
        "independently_reverified_hmac_forwarding":False,
        "orders_submitted":0,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    a=p.parse_args(argv)
    try:r=inspect(a.root)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as exc:
        r={"verdict":"AUDIT_REFUSED","failure_class":type(exc).__name__,"orders_submitted":0}
    print(json.dumps(r,sort_keys=True))
    return 0 if r["verdict"]=="PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
