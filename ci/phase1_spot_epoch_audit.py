"""Read-only independent acceptance of TWO segregated offline Spot epochs.

Requires a previously failed depth sync epoch with SHA-verified sealed prefix,
permanent UNKNOWN observation boundary, and separately qualified fresh source
with its own authenticated HMAC receiver/checkpoint chain. This report never
claims real Binance reconnect or inter-epoch completeness.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from ci.phase1_spot_epoch_restart import (
    inspect_quarantined_prefix, _fresh_first_receipt, _SCHEMA,
)
from ci.phase1_rotating_hmac_audit import inspect as audit_hmac

def inspect(root:Path)->dict:
    if (not isinstance(root,Path) or not root.is_absolute()
        or not root.is_dir() or root.is_symlink() or root!=root.resolve()):
        raise ValueError("unsafe epoch evidence root")
    unknown_file=root/"unknown_boundary.json"
    accepted_file=root/"offline_epoch_restart_evidence.json"
    if (unknown_file.is_symlink() or accepted_file.is_symlink()
        or (root/"epoch_recovery_quarantine.json").exists()):
        raise ValueError("epoch boundary is unsafe or still quarantined")
    unknown=json.loads(unknown_file.read_text(encoding="utf-8"))
    report=json.loads(accepted_file.read_text(encoding="utf-8"))
    source=inspect_quarantined_prefix(root/"epoch_0000")
    newer=audit_hmac(root/"epoch_0001",co_run=True)
    if newer["verdict"]!="PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY":
        raise ValueError("second epoch independent HMAC/SQLite verification refused")
    first_report=json.loads((root/"epoch_0000"/"aggregate_report.json").read_text())
    later_report=json.loads((root/"epoch_0001"/"aggregate_report.json").read_text())
    later_hmac=json.loads((root/"epoch_0001"/"hmac_corun_aggregate.json").read_text())
    old_ids={s["session_id"] for s in first_report["segments"]}
    fresh_ids={s["session_id"] for s in later_report["segments"]}
    fresh_first=_fresh_first_receipt(root/"epoch_0001")
    start=source["trusted_prefix_last_local_receipt_ms"]
    expected_unknown={
        "schema":_SCHEMA,"state":"UNKNOWN_COVERAGE_PENDING_FRESH_EPOCH",
        "first_epoch_failure_code":source["failure_code"],
        "first_epoch_prefix_chain":source["sha_chain_end"],
        "first_epoch_prefix_sealed_frames":source["verified_sealed_prefix_ws_frames"],
        "first_epoch_unsealed_frames_not_certified":source["observed_frames_unsealed_or_unaccounted"],
        "unknown_begin_local_ms":start,
        "verified_gap_free_between_epochs":False,
        "previous_book_reused":False,
        "previous_socket_reused":False,
        "new_rest_snapshot_required":True,"orders_submitted":0,
    }
    if unknown!=expected_unknown:
        raise ValueError("source epoch UNKNOWN boundary mutated or incomplete")
    if (not old_ids or not fresh_ids or not old_ids.isdisjoint(fresh_ids)
        or newer["segments"]<2
        or later_report.get("verdict")!="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY"
        or later_report.get("source_socket_count")!=1
        or later_report.get("produced_ws_frames")!=later_report.get("committed_ws_frames")
        or later_hmac.get("source_reader_attached_during_hmac") is not True
        or later_hmac.get("orders_submitted")!=0
        or fresh_first<start):
        raise ValueError("new independent source epoch cannot be certified")
    expected_report={
        "schema":_SCHEMA,"verdict":"PASSED_OFFLINE_NEW_SOURCE_EPOCH_ISOLATION_ONLY",
        "original_broken_epoch_quarantined":True,
        "new_epoch_original_hmac_checkpoint_independently_audited":True,
        "first_epoch_verified_prefix_frames":source["verified_sealed_prefix_ws_frames"],
        "first_epoch_unsealed_frames_not_certified":source["observed_frames_unsealed_or_unaccounted"],
        "new_epoch_originals":newer["raw_original_events"],
        "new_epoch_reconstructed_projections":newer["source_projections"],
        "new_epoch_receiver_receipts":newer["receiver_owned_receipts"],
        "new_epoch_checkpoints":newer["forward_checkpoints"],
        "unknown_begin_local_ms":start,
        "unknown_end_first_fresh_receipt_ms":fresh_first,
        "new_epoch_source_session_ids_disjoint":True,
        "new_epoch_fresh_snapshots":newer["segments"],
        "unknown_market_coverage_between_epochs":True,
        "gap_free_between_epochs_certified":False,
        "live_public_reconnection_tested":False,
        "full_continuous_market_hmac_certified":False,
        "institutional_phase1_complete":False,
        "orders_submitted":0,
    }
    if report!=expected_report:
        raise ValueError("epoch restart aggregate changed since independent original/ACK audit")
    if (newer["source_projections"]<=0
        or newer["source_projections"]!=newer["receiver_owned_receipts"]
        or newer["receiver_owned_receipts"]!=newer["forward_checkpoints"]):
        raise ValueError("new source receiver ACK/checkpoint parity cannot be proven")
    return {
        "schema":"econith-phase1-segregated-source-epoch-independent-audit-v1",
        "verdict":"PASSED_LOCAL_EPOCH_ISOLATION_WITH_UNKNOWN_COVERAGE_ONLY",
        "previous_failed_source_prefix_frames_sha_verified":source["verified_sealed_prefix_ws_frames"],
        "previous_epoch_unsealed_frames_not_certified":source["observed_frames_unsealed_or_unaccounted"],
        "new_epoch_originals_sha_verified":newer["raw_original_events"],
        "new_epoch_receiver_receipts":newer["receiver_owned_receipts"],
        "new_epoch_checkpoint_chain":newer["forward_checkpoints"],
        "new_epoch_rest_snapshots":newer["segments"],
        "unknown_coverage_begin_local_receipt_ms":start,
        "unknown_coverage_end_first_fresh_local_receipt_ms":fresh_first,
        "inter_epoch_gap_free":False,
        "external_binance_reconnect_proven":False,
        "orders_submitted":0,
        "institutional_phase1_complete":False,
    }

def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    a=p.parse_args(argv)
    try:
        result=inspect(a.root)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,IndexError) as exc:
        result={"verdict":"EPOCH_AUDIT_REFUSED","failure_class":type(exc).__name__,
                "inter_epoch_gap_free":False,"orders_submitted":0}
    print(json.dumps(result,sort_keys=True))
    return 0 if result["verdict"]=="PASSED_LOCAL_EPOCH_ISOLATION_WITH_UNKNOWN_COVERAGE_ONLY" else 2

if __name__=="__main__":
    sys.exit(main())
