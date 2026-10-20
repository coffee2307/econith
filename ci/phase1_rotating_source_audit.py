"""Independent read-only forensic reconciliation of bounded rotating originals."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from ci.phase1_rotating_spot_source import _INITIAL_CHAIN,_SCHEMA,roll_hash


def inspect(root: Path) -> dict:
    if (not isinstance(root,Path) or not root.is_absolute()
        or root!=root.resolve() or root.is_symlink() or not root.is_dir()):
        raise ValueError("unsafe rotation evidence scope")
    if (root/".forward_producer.lock").exists():
        raise ValueError("active/orphaned owner marker; operator review required")
    report_path=root/"aggregate_report.json"
    if report_path.is_symlink():
        raise ValueError("aliased report")
    report=json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("schema")!=_SCHEMA:
        raise ValueError("unexpected rotation report")
    declared=report.get("segments")
    segments_root=root/"segments"
    if (not segments_root.is_dir() or segments_root.is_symlink()
        or type(declared) is not list or not 2<=len(declared)<=32):
        raise ValueError("rotation segments are absent/out of range")
    folders=sorted(x for x in segments_root.iterdir() if x.is_dir())
    if len(folders)!=len(declared) or any(x.is_symlink() for x in folders):
        raise ValueError("segment filesystem inventory mismatch")
    chain=_INITIAL_CHAIN
    event_count=0
    frame_count=0
    last_ms=None
    for index,(folder,decl) in enumerate(zip(folders,declared)):
        if folder.name!=f"{index:04d}" or type(decl) is not dict:
            raise ValueError("unexpected rotating journal directory")
        seal_path=folder/"seal.json"
        db_path=folder/"original.sqlite"
        if (seal_path.is_symlink() or db_path.is_symlink()
            or not seal_path.is_file() or not db_path.is_file()):
            raise ValueError("missing sealed original evidence")
        sealed=json.loads(seal_path.read_text(encoding="utf-8"))
        if sealed!=decl or sealed["index"]!=index or sealed["chain_start"]!=chain:
            raise ValueError("segment seal differs from declared source inventory")
        with sqlite3.connect(f"file:{db_path}?mode=ro",uri=True) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("rotating SQLite forensic integrity mismatch")
            sessions=db.execute("SELECT * FROM offline_spot_capture_sessions").fetchall()
            records=db.execute(
                "SELECT capture_id,session_id,kind,received_at_ms,raw_blob,sha256 "
                "FROM offline_spot_capture_records ORDER BY capture_id"
            ).fetchall()
            intents=db.execute(
                "SELECT capture_id,session_id,kind,received_at_ms,sha256 "
                "FROM offline_forward_capture_intents ORDER BY capture_id"
            ).fetchall()
        if (len(sessions)!=1 or sessions[0]["session_id"]!=sealed["session_id"]
            or sessions[0]["closed_at_ms"] is None
            or sessions[0]["source"]!="binance_public_simulation"
            or sessions[0]["symbol"]!="BTCUSDT"
            or len(records)!=len(intents)
            or len(records)!=sealed["original_events"]):
            raise ValueError("unsealed source session/count divergence")
        record_kinds={"depthUpdate":0,"aggTrade":0,"snapshot":0}
        for rec,intent in zip(records,intents):
            if (rec["capture_id"]!=intent["capture_id"]
                or rec["session_id"]!=intent["session_id"]
                or rec["kind"]!=intent["kind"]
                or rec["received_at_ms"]!=intent["received_at_ms"]
                or rec["sha256"]!=intent["sha256"]
                or hashlib.sha256(rec["raw_blob"]).hexdigest()!=rec["sha256"]):
                raise ValueError("original/intent SHA or receipt divergence")
            if rec["kind"] not in record_kinds:
                raise ValueError("unknown source event type")
            if rec["kind"]!="snapshot":
                if last_ms is not None and rec["received_at_ms"]<last_ms:
                    raise ValueError("upstream WebSocket receipt chronology regressed")
                last_ms=rec["received_at_ms"]
            record_kinds[rec["kind"]]+=1
            event_count+=1
            chain=roll_hash(chain,event_count,bytes(rec["raw_blob"]))
        ws_count=record_kinds["depthUpdate"]+record_kinds["aggTrade"]
        if (record_kinds["snapshot"]!=1
            or sealed["snapshot_captures"]!=1
            or sealed["websocket_frames"]!=ws_count
            or sealed["original_events"]!=ws_count+1
            or sealed["depth_status"]!="SYNCED"
            or sealed["queue_peak"]>=128
            or sealed["first_frame_ordinal"]!=frame_count+1
            or sealed["last_frame_ordinal"]!=frame_count+ws_count
            or sealed["chain_end"]!=chain):
            raise ValueError("rotated segment failed source/snapshot seal")
        frame_count+=ws_count
    status=(
        report.get("verdict")=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY"
        and report.get("mode")=="SIMULATION"
        and report.get("source_socket_count")==1
        and report.get("source_reader_task_count")==1
        and report.get("owner_fence_held") is True
        and report.get("produced_ws_frames")==frame_count
        and report.get("committed_ws_frames")==frame_count
        and report.get("source_queue_capacity")==64
        and 1<=report.get("source_queue_high_watermark",64)<64
        and report.get("adapter_queue_capacity")==128
        and report.get("event_freshness_cap_ms")==5000
        and report.get("duration_target_reached") is True
        and type(report.get("duration_stream_elapsed_s")) in (int,float)
        and report["duration_stream_elapsed_s"]>=report["target_duration_s"]-0.05
        and type(report.get("terminal_source_silence_s")) in (int,float)
        and 0<=report["terminal_source_silence_s"]<=5.0
        and report.get("chain_end")==chain
        and report.get("HMAC_downstream_tested") is False
        and report.get("exchange_history_completeness_verified") is False
        and report.get("venue_losslessness_verified") is False
        and report.get("default_market_ingress_cutover") is False
        and report.get("institutional_phase1_complete") is False
        and report.get("orders_submitted")==0
    )
    return {
        "verdict":"PASSED_BOUNDED_LOCAL_SOURCE_ROTATION_AUDIT_ONLY"
                  if status else "ROTATION_NOT_ACCEPTED",
        "source_socket_count":1,
        "original_journal_count":len(declared),
        "raw_original_events":event_count,
        "source_frames":frame_count,
        "snapshots":len(declared),
        "original_intent_sha_mismatches":0,
        "committed_frame_gaps_within_local_reader":0 if status else None,
        "chain_end":chain,
        "HMAC_downstream_audited":False,
        "exchange_history_completeness_certified":False,
        "hardware_power_loss_certified":False,
        "orders_submitted":0,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    args=p.parse_args(argv)
    try:out=inspect(args.root)
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,IndexError) as exc:
        out={"verdict":"AUDIT_REFUSED","failure_class":type(exc).__name__,
             "orders_submitted":0}
    print(json.dumps(out,sort_keys=True))
    return 0 if out["verdict"]=="PASSED_BOUNDED_LOCAL_SOURCE_ROTATION_AUDIT_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
