"""Read-only forensic inspection of a finite spool or hard-killed producer.

Does NOT remove an orphan owner fence, reopen WSS, replay, certify HMAC
cryptography, or change any original/derived/outbox/cursor record.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import sqlite3
from pathlib import Path


def _query_one(path: Path, sql: str):
    if not path.is_file() or path.is_symlink():
        raise ValueError("required offline evidence ledger missing")
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,
                                 timeout=3.0)) as con:
        return con.execute(sql).fetchone()


def inspect(root: Path) -> dict:
    if (not isinstance(root, Path) or not root.is_absolute()
        or not root.is_dir() or root != root.resolve() or root.is_symlink()):
        raise ValueError("absolute existing unaliased evidence root required")
    originals,received_min,received_max=_query_one(
        root/"original.sqlite",
        "SELECT COUNT(*),MIN(received_at_ms),MAX(received_at_ms) FROM offline_spot_capture_records",
    )
    cursors=_query_one(
        root/"projection_cursor.sqlite",
        "SELECT COUNT(*),MAX(last_capture_id) FROM spot_projection_cursor",
    )
    sessions=_query_one(
        root/"original.sqlite",
        "SELECT COUNT(*),SUM(CASE WHEN closed_at_ms IS NULL THEN 1 ELSE 0 END) FROM offline_spot_capture_sessions",
    )
    receipts=_query_one(
        root/"independent_receiver.sqlite",
        "SELECT COUNT(*) FROM offline_independent_projection_receipts",
    )[0]
    checkpoints=_query_one(
        root/"forward_checkpoints.sqlite",
        "SELECT COUNT(*) FROM forward_checkpoint",
    )[0]
    if cursors[0]!=1 or sessions[0]!=1:
        raise ValueError("ambiguous source/cursor session scope")
    count=int(originals)
    cursor=int(cursors[1] or 0)
    unprocessed=count-cursor
    orphan=(root/".forward_producer.lock").exists()
    if unprocessed<0 or cursor<0:
        raise ValueError("cursor beyond committed original inventory")
    # No source/ACK certification from counts. Full independent integrity
    # and HMAC proofs are separate mandatory acceptance gates.
    status=("ORPHAN_FENCE_REQUIRES_OPERATOR_REVIEW" if orphan else
            "OPEN_OR_ABORTED_SOURCE" if sessions[1] else
            "CLOSED_DIAGNOSTIC_ONLY")
    return {
        "schema":"econith-phase1-spool-forensic-readonly-v1",
        "source_originals_committed":count,
        "durable_projector_cursor":cursor,
        "originals_not_projected":unprocessed,
        "independent_receiver_rows":int(receipts),
        "checkpoint_rows":int(checkpoints),
        "receiver_checkpoint_counts_match":receipts==checkpoints,
        "original_session_open_or_aborted":bool(sessions[1]),
        "orphan_owner_fence_present":orphan,
        "inspection_status":status,
        "source_continuity_certified":False,
        "receiver_hmac_cryptography_certified_by_this_inspector":False,
        "orders_submitted":0,
        "mutations_performed":False,
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trial-dir",type=Path,required=True)
    args=p.parse_args(argv)
    try:
        result=inspect(args.trial_dir)
    except (ValueError,sqlite3.DatabaseError,OSError) as exc:
        print(json.dumps({
            "inspection_status":"REFUSED_UNTRUSTED_OR_INCOMPLETE_INPUT",
            "error_class":type(exc).__name__,
            "source_continuity_certified":False,
            "mutations_performed":False,
        }))
        return 2
    print(json.dumps(result,sort_keys=True,allow_nan=False))
    return 0 if (not result["orphan_owner_fence_present"]
                 and not result["original_session_open_or_aborted"]
                 and result["originals_not_projected"]==0
                 and result["receiver_checkpoint_counts_match"]) else 2


if __name__=="__main__":
    raise SystemExit(main())
