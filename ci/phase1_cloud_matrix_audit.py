"""Cloud review: read-only forensic counts for each finite isolated source.

Creates an aggregate-only cloud_audit.json; does NOT upload raw SQLite or claim
cryptographic re-verification, continuous venue history, or production safety.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
from pathlib import Path

from ci.phase1_spool_forensic_inspect import inspect


def _scalar(path: Path, sql: str) -> int:
    if not path.is_file() or path.is_symlink():
        raise ValueError("SQLite evidence missing/aliased")
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        return int(db.execute(sql).fetchone()[0])


def verify(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir() or root != root.resolve():
        raise ValueError("untrusted output root")
    report_path=root/"matrix_report.json"
    if not report_path.is_file() or report_path.is_symlink():
        return {
            "schema":"econith-phase1-cloud-audit-v1",
            "result":"NO_COMPLETED_MATRIX_REPORT",
            "passed":0,
            "audit_performed":False,
            "source_continuity_certified":False,
            "orders_submitted":0,
        }
    report=json.loads(report_path.read_text(encoding="utf-8"))
    checks=[]
    for i in range(1,4):
        row={"iteration":i,"read_only":True,"pass":False}
        path=root/f"attempt_{i:03d}"
        if not path.is_dir():
            row["status"]="NOT_STARTED"
            checks.append(row)
            continue
        try:
            status=inspect(path)
            original=path/"original.sqlite"
            noriginal=_scalar(original,
                "SELECT COUNT(*) FROM offline_spot_capture_records")
            nintent=_scalar(original,
                "SELECT COUNT(*) FROM offline_forward_capture_intents")
            mismatches=_scalar(original,
                "SELECT COUNT(*) FROM offline_spot_capture_records r "
                "LEFT JOIN offline_forward_capture_intents i "
                "ON i.capture_id=r.capture_id "
                "WHERE i.capture_id IS NULL OR i.sha256<>r.sha256")
            for name in ("projection_cursor.sqlite","independent_receiver.sqlite",
                         "forward_checkpoints.sqlite"):
                _scalar(path/name,"SELECT 1")
            expected=next((s for s in report.get("segments",[])
                           if s.get("iteration")==i),None)
            row.update({
                "source_originals":noriginal,
                "source_intents":nintent,
                "source_intent_mismatches":mismatches,
                "cursor":status["durable_projector_cursor"],
                "receiver_rows":status["independent_receiver_rows"],
                "checkpoint_rows":status["checkpoint_rows"],
                "owner_fence_present":status["owner_fence_present"],
                "source_closed":not status["original_session_open_or_aborted"],
            })
            row["pass"]=bool(
                expected and expected.get("status")=="PASSED"
                and noriginal==nintent==status["durable_projector_cursor"]==1001
                and mismatches==0 and status["independent_receiver_rows"]>0
                and status["receiver_checkpoint_counts_match"]
                and not status["owner_fence_present"]
                and not status["original_session_open_or_aborted"]
                and expected.get("receiver_acks")==status["independent_receiver_rows"]
                and expected.get("checkpoints")==status["checkpoint_rows"]
                and expected.get("max_lag_ms",99999)<=5000
                and expected.get("queue_peak",128)<128
                and expected.get("orders_submitted")==0
            )
            row["status"]="INDEPENDENT_COUNTS_MATCH" if row["pass"] else "REFUSED_INCOMPLETE_LEDGER"
        except (ValueError,KeyError,TypeError,sqlite3.Error,OSError) as exc:
            row["status"]="AUDIT_UNAVAILABLE_OR_INVALID"
            row["error_class"]=type(exc).__name__
        checks.append(row)
    accepted=sum(int(x["pass"]) for x in checks)
    return {
        "schema":"econith-phase1-cloud-audit-v1",
        "result":("PASS_FINITE_ONLY" if accepted==3 and report.get("passed")==3
                  and report.get("verdict")=="PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY"
                  else "NOT_ACCEPTED"),
        "passed":accepted,
        "audit_performed":True,
        "checks":checks,
        "source_continuity_certified":False,
        "hmac_signatures_cryptographically_reverified_by_audit":False,
        "institutional_phase1_complete":False,
        "orders_submitted":0,
        "raw_sqlite_uploaded":False,
    }


def main(argv=None)->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    a=parser.parse_args(argv)
    try:
        result=verify(a.root)
    except (OSError,ValueError,TypeError,json.JSONDecodeError) as exc:
        result={
            "schema":"econith-phase1-cloud-audit-v1",
            "result":"AUDIT_REFUSED",
            "error_class":type(exc).__name__,
            "passed":0,
            "source_continuity_certified":False,
            "orders_submitted":0,
        }
    if a.root.is_dir() and not a.root.is_symlink():
        (a.root/"cloud_audit.json").write_text(
            json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n",
            encoding="utf-8",
        )
    print(json.dumps(result,sort_keys=True,allow_nan=False),flush=True)
    return 0 if result["result"]=="PASS_FINITE_ONLY" else 2


if __name__=="__main__":
    raise SystemExit(main())
