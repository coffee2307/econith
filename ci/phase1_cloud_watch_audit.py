"""Read-only multi-session cloud watch audit. Never asserts source continuity."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from ci.phase1_spool_forensic_inspect import inspect
from ci.phase1_cloud_matrix_audit import _scalar


def verify(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir() or root != root.resolve():
        raise ValueError("invalid isolated watch root")
    file=root/"watch_report.json"
    if not file.is_file() or file.is_symlink():
        return {"schema":"econith-phase1-cloud-watch-audit-v1",
                "verdict":"WATCH_REPORT_UNAVAILABLE","passed_sources":0,
                "source_continuity_certified":False,"orders_submitted":0}
    report=json.loads(file.read_text(encoding="utf-8"))
    results=[]
    for segment in report.get("segments",[]):
        number=int(segment["index"])
        path=root/f"attempt_{number:03d}"
        result={"attempt":number,"pass":False,"status":"NOT_AUDITED"}
        try:
            proof=inspect(path)
            a=_scalar(path/"original.sqlite",
                      "SELECT COUNT(*) FROM offline_spot_capture_records")
            intents=_scalar(path/"original.sqlite",
                      "SELECT COUNT(*) FROM offline_forward_capture_intents")
            wrong=_scalar(path/"original.sqlite",
                 "SELECT COUNT(*) FROM offline_spot_capture_records r "
                 "LEFT JOIN offline_forward_capture_intents i "
                 "ON i.capture_id=r.capture_id "
                 "WHERE i.capture_id IS NULL OR i.sha256<>r.sha256")
            for name in ("projection_cursor.sqlite","independent_receiver.sqlite",
                         "forward_checkpoints.sqlite"):
                _scalar(path/name,"SELECT 1")
            result.update({"originals":a,"intents":intents,
                           "intent_sha_mismatches":wrong,
                           "cursor":proof["durable_projector_cursor"],
                           "receiver_acks":proof["independent_receiver_rows"],
                           "checkpoints":proof["checkpoint_rows"],
                           "owner_fence_present":proof["owner_fence_present"]})
            result["pass"]=bool(
                segment.get("status")=="PASSED_FINITE_ONLY"
                and a==intents==proof["durable_projector_cursor"]==1001
                and wrong==0 and proof["receiver_checkpoint_counts_match"]
                and proof["independent_receiver_rows"]>0
                and segment.get("receiver_acks")==proof["independent_receiver_rows"]
                and segment.get("checkpoints")==proof["checkpoint_rows"]
                and not proof["owner_fence_present"]
                and not proof["original_session_open_or_aborted"]
                and segment.get("max_age_ms",99999)<=5000
                and segment.get("queue_peak",128)<128
                and segment.get("orders_submitted",0)==0
            )
            result["status"]="INDEPENDENT_LEDGER_COUNTS_MATCH" if result["pass"] else "INCOMPLETE_NOT_CERTIFIED"
        except (OSError,ValueError,TypeError,KeyError,sqlite3.Error) as exc:
            result["status"]="UNVERIFIABLE"
            result["error_class"]=type(exc).__name__
        results.append(result)
    good=sum(bool(row["pass"]) for row in results)
    accepted=bool(
        report.get("verdict")=="PASSED_FINITE_10MIN_MULTISEGMENT_RESEARCH_WATCH_ONLY"
        and report.get("target_reached") is True
        and report.get("watch_elapsed_s",0)>=600
        and report.get("watch_elapsed_s",99999)<=900
        and len(results)>0 and good==len(results)
        and report.get("passed_segments")==good
        and report.get("segment_boundaries_without_verified_coverage")==good-1
        and report.get("history_continuity_verified") is False
        and report.get("continuous_live_market") is False
        and report.get("orders_submitted")==0
    )
    return {
        "schema":"econith-phase1-cloud-watch-audit-v1",
        "verdict":"PASSED_FINITE_SEGMENTED_ONLY" if accepted else "NOT_ACCEPTED",
        "passed_sources":good,
        "total_sources":len(results),
        "watch_elapsed_s":report.get("watch_elapsed_s"),
        "source_intervals_not_observed":report.get("segment_boundaries_without_verified_coverage"),
        "segments":results,
        "source_continuity_certified":False,
        "hmac_signatures_cryptographically_reverified_by_audit":False,
        "institutional_phase1_complete":False,
        "orders_submitted":0,
        "raw_original_ledgers_uploaded":False,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",required=True,type=Path)
    args=p.parse_args(argv)
    try:
        result=verify(args.root)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error) as exc:
        result={"schema":"econith-phase1-cloud-watch-audit-v1",
                "verdict":"AUDIT_REFUSED","error_class":type(exc).__name__,
                "source_continuity_certified":False,"orders_submitted":0}
    if args.root.is_dir() and not args.root.is_symlink():
        (args.root/"cloud_watch_audit.json").write_text(
            json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n",
            encoding="utf-8",
        )
    print(json.dumps(result,sort_keys=True,allow_nan=False),flush=True)
    return 0 if result["verdict"]=="PASSED_FINITE_SEGMENTED_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
