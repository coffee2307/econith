"""Phase1 isolated Spot source-epoch restart: offline evidence, no live auto-reconnect.

First source epoch MUST fail close on a depth snapshot/diff bridge. Its trusted
sealed prefix is independently SHA audited; an explicit UNKNOWN observation
boundary is fsynced before a separate source socket/lease is opened. The fresh
epoch must pass independent original/ACK/three-observer/checkpoint audit.
This never certifies inter-epoch coverage, continuity or automatic recovery.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from ci.phase1_rotating_spot_source import (
    _INITIAL_CHAIN, _SCHEMA as _SOURCE_SCHEMA, roll_hash, run_rotation,
)
from ci.phase1_rotating_corun_hmac import co_run_rotating_hmac
from ci.phase1_rotating_hmac_audit import inspect as audit_hmac
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease

_SCHEMA="econith-phase1-explicit-spot-epoch-restart-v1"
_ALLOWED_SOURCE_BREAKS=frozenset(("BOOK_NOT_SYNCED","FRAME_LIMIT_UNSYNCED","ROTATED_SEAL_UNSYNCED"))


def _json_exclusive(path:Path,obj:dict)->None:
    with path.open("x",encoding="utf-8") as f:
        json.dump(obj,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def inspect_quarantined_prefix(root:Path)->dict:
    """Audits ONLY closed/ordered pre-failure source segments, never the tail."""
    if (not root.is_absolute() or root!=root.resolve()
        or root.is_symlink() or (root/".forward_producer.lock").exists()
        or (root/"aggregate_report.json").is_symlink()):
        raise ValueError("unsafe or still-owned failed source")
    report=json.loads((root/"aggregate_report.json").read_text())
    code=report.get("failure_code")
    segments=report.get("segments")
    if (report.get("schema")!=_SOURCE_SCHEMA
        or report.get("verdict")!="FAILED_OR_QUARANTINED"
        or code not in _ALLOWED_SOURCE_BREAKS
        or report.get("mode")!="SIMULATION"
        or report.get("source_socket_count")!=1
        or report.get("source_reader_task_count")!=1
        or report.get("orders_submitted")!=0
        or type(segments) is not list or not 1<=len(segments)<32
        or (root/"hmac_corun_aggregate.json").exists()):
        raise ValueError("not a qualified failed source epoch")
    folders=sorted(x for x in (root/"segments").iterdir() if x.is_dir())
    if (len(folders)<len(segments) or len(folders)>len(segments)+1
        or any(x.is_symlink() for x in folders)):
        raise ValueError("failed source journal inventory diverges")
    chain=_INITIAL_CHAIN
    ordinal=0
    frames=0
    last_frame_receipt=None
    identities=set()
    for i,seal in enumerate(segments):
        sd=root/"segments"/f"{i:04d}"
        file=sd/"original.sqlite"
        if (type(seal) is not dict or seal.get("index")!=i
            or seal.get("depth_status")!="SYNCED"
            or seal.get("chain_start")!=chain
            or sd.is_symlink() or file.is_symlink()
            or not file.is_file() or (sd/"seal.json").is_symlink()
            or json.loads((sd/"seal.json").read_text())!=seal):
            raise ValueError("sealed failed-epoch source prefix does not match")
        with sqlite3.connect(file.resolve().as_uri()+"?mode=ro",uri=True) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
                raise ValueError("corrupt first-epoch source prefix")
            session=db.execute("SELECT * FROM offline_spot_capture_sessions").fetchall()
            raw=db.execute(
                "SELECT capture_id,session_id,kind,received_at_ms,raw_blob,sha256 "
                "FROM offline_spot_capture_records ORDER BY capture_id"
            ).fetchall()
            intents=db.execute(
                "SELECT capture_id,session_id,kind,received_at_ms,sha256 "
                "FROM offline_forward_capture_intents ORDER BY capture_id"
            ).fetchall()
        sid=seal["session_id"]
        if (sid in identities or not sid.startswith("rec_rot_")
            or len(session)!=1 or session[0]["session_id"]!=sid
            or session[0]["closed_at_ms"] is None
            or len(raw)!=len(intents)
            or len(raw)!=seal["original_events"]
            or seal["snapshot_captures"]!=1
            or seal["original_events"]!=seal["websocket_frames"]+1
            or seal["first_frame_ordinal"]!=frames+1):
            raise ValueError("first-epoch original and capture-intent inventory diverges")
        identities.add(sid)
        seen_snap=0
        local_ws=0
        for r,t in zip(raw,intents):
            if (r["capture_id"]!=t["capture_id"]
                or r["session_id"]!=t["session_id"]
                or r["kind"]!=t["kind"]
                or r["received_at_ms"]!=t["received_at_ms"]
                or r["sha256"]!=t["sha256"]
                or hashlib.sha256(r["raw_blob"]).hexdigest()!=r["sha256"]):
                raise ValueError("failed epoch original/intent SHA mismatch")
            if r["kind"]=="snapshot":
                seen_snap+=1
            elif r["kind"] in ("aggTrade","depthUpdate"):
                local_ws+=1
                if (last_frame_receipt is not None
                    and r["received_at_ms"]<last_frame_receipt):
                    raise ValueError("failed source clock regressed")
                last_frame_receipt=r["received_at_ms"]
            else:
                raise ValueError("unknown source original classification")
            ordinal+=1
            chain=roll_hash(chain,ordinal,bytes(r["raw_blob"]))
        if seen_snap!=1 or local_ws!=seal["websocket_frames"]:
            raise ValueError("failed source prefix cannot be proven complete")
        frames+=local_ws
        if (seal["last_frame_ordinal"]!=frames
            or seal["chain_end"]!=chain):
            raise ValueError("failed prefix chain/ordinal mismatch")
    if (frames!=report.get("committed_ws_frames")
        or chain!=report.get("chain_end")
        or type(report.get("produced_ws_frames")) is not int
        or report["produced_ws_frames"]<frames
        or last_frame_receipt is None):
        raise ValueError("failed prefix source aggregate does not reconcile")
    return {
        "failure_code":code,
        "verified_sealed_prefix_segments":len(segments),
        "verified_sealed_prefix_ws_frames":frames,
        "verified_sealed_originals":ordinal,
        "sha_chain_end":chain,
        "trusted_prefix_last_local_receipt_ms":last_frame_receipt,
        "unsealed_tail_present":len(folders)>len(segments),
        "observed_frames_unsealed_or_unaccounted":report["produced_ws_frames"]-frames,
        "source_history_complete":False,
        "orders_submitted":0,
    }


def _fresh_first_receipt(root:Path)->int:
    sd=root/"segments"/"0000"/"original.sqlite"
    if not sd.is_file() or sd.is_symlink():
        raise ValueError("new epoch has no durable original")
    with sqlite3.connect(sd.resolve().as_uri()+"?mode=ro",uri=True) as db:
        found=db.execute(
            "SELECT MIN(received_at_ms) FROM offline_spot_capture_records "
            "WHERE kind IN ('aggTrade','depthUpdate')"
        ).fetchone()[0]
    if type(found) is not int or found<=0:
        raise ValueError("new epoch original receipt missing")
    return found


async def run_offline_epoch_restart(
    root:Path,*,first_lease_factory,second_lease_factory,clock_preflight,
    duration_s:float=3.8,max_frames_per_segment:int=12,
    explicit_offline_operator_restart_approval:bool=False,
) -> dict:
    """Two separate source sockets/SQLite roots. NEVER retry within one epoch."""
    if explicit_offline_operator_restart_approval is not True:
        raise PermissionError("explicit operator-reviewed offline epoch restart required")
    if (not isinstance(root,Path) or not root.is_absolute()
        or root.exists() or root.is_symlink()
        or root!=root.resolve()
        or not callable(first_lease_factory)
        or not callable(second_lease_factory)
        or first_lease_factory is second_lease_factory
        or first_lease_factory is open_pinned_public_spot_lease
        or second_lease_factory is open_pinned_public_spot_lease
        or not callable(clock_preflight)
        or type(duration_s) not in (float,int) or not .8<=duration_s<=4
        or type(max_frames_per_segment) is not int
        or not 8<=max_frames_per_segment<=20):
        raise ValueError("epoch recovery restricted to two bounded offline source fixtures")
    root.mkdir(mode=0o700)
    first=root/"epoch_0000"
    second=root/"epoch_0001"
    try:
        failed=await run_rotation(
            first,allow_public_market_data=True,
            duration_s=duration_s,max_frames_per_segment=max_frames_per_segment,
            lease_factory=first_lease_factory,clock_preflight=clock_preflight,
            _fixture=True,
        )
        # A source PASS is NOT a recovery test and never authorizes second
        # source. There is no silent fallback/retry over a healthy epoch.
        if failed.get("verdict")=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY":
            raise ValueError("first epoch did not fail; restart not authorized")
        trusted=inspect_quarantined_prefix(first)
        boundary={
            "schema":_SCHEMA,"state":"UNKNOWN_COVERAGE_PENDING_FRESH_EPOCH",
            "first_epoch_failure_code":trusted["failure_code"],
            "first_epoch_prefix_chain":trusted["sha_chain_end"],
            "first_epoch_prefix_sealed_frames":trusted["verified_sealed_prefix_ws_frames"],
            "first_epoch_unsealed_frames_not_certified":trusted["observed_frames_unsealed_or_unaccounted"],
            "unknown_begin_local_ms":trusted["trusted_prefix_last_local_receipt_ms"],
            "verified_gap_free_between_epochs":False,
            "previous_book_reused":False,
            "previous_socket_reused":False,
            "new_rest_snapshot_required":True,
            "orders_submitted":0,
        }
        _json_exclusive(root/"unknown_boundary.json",boundary)
        recovered=await co_run_rotating_hmac(
            second,duration_s=duration_s,
            max_frames_per_segment=max_frames_per_segment,
            lease_factory=second_lease_factory,clock_preflight=clock_preflight,
            explicit_offline_fixture_approval=True,
            receiver_batch_size=8,reuse_receiver_connection=True,
        )
        if recovered.get("verdict")!="PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY":
            raise ValueError(
                "fresh source epoch not accepted; source_class="
                +str(recovered.get("source_failure_class"))
                +", source_code="+str(recovered.get("source_failure_code"))
            )
        independent=audit_hmac(second,co_run=True)
        if independent.get("verdict")!="PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY":
            raise ValueError("fresh epoch HMAC independent audit refused")
        first_new=_fresh_first_receipt(second)
        if first_new<boundary["unknown_begin_local_ms"]:
            raise ValueError("new source first receipt predates last trusted old source")
        old_sessions={s["session_id"] for s in failed["segments"]}
        new_sessions={s["session_id"] for s in recovered["segments"]}
        if old_sessions & new_sessions or not new_sessions:
            raise ValueError("epoch session identity overlap")
        summary={
            "schema":_SCHEMA,"verdict":"PASSED_OFFLINE_NEW_SOURCE_EPOCH_ISOLATION_ONLY",
            "original_broken_epoch_quarantined":True,
            "new_epoch_original_hmac_checkpoint_independently_audited":True,
            "first_epoch_verified_prefix_frames":trusted["verified_sealed_prefix_ws_frames"],
            "first_epoch_unsealed_frames_not_certified":trusted["observed_frames_unsealed_or_unaccounted"],
            "new_epoch_originals":independent["raw_original_events"],
            "new_epoch_reconstructed_projections":independent["source_projections"],
            "new_epoch_receiver_receipts":independent["receiver_owned_receipts"],
            "new_epoch_checkpoints":independent["forward_checkpoints"],
            "unknown_begin_local_ms":boundary["unknown_begin_local_ms"],
            "unknown_end_first_fresh_receipt_ms":first_new,
            "new_epoch_source_session_ids_disjoint":True,
            "new_epoch_fresh_snapshots":independent["segments"],
            "unknown_market_coverage_between_epochs":True,
            "gap_free_between_epochs_certified":False,
            "live_public_reconnection_tested":False,
            "full_continuous_market_hmac_certified":False,
            "institutional_phase1_complete":False,
            "orders_submitted":0,
        }
        if (summary["new_epoch_reconstructed_projections"]<=0
            or summary["new_epoch_reconstructed_projections"]!=summary["new_epoch_receiver_receipts"]
            or summary["new_epoch_receiver_receipts"]!=summary["new_epoch_checkpoints"]
            or summary["new_epoch_fresh_snapshots"]<2):
            raise ValueError("new source epoch original HMAC ACK parity refused")
        _json_exclusive(root/"offline_epoch_restart_evidence.json",summary)
        return summary
    except BaseException as exc:
        _json_exclusive(root/"epoch_recovery_quarantine.json",{
            "schema":_SCHEMA,"verdict":"REFUSED_UNKNOWN_COVERAGE_UNRESOLVED",
            "error_class":type(exc).__name__,
            "gap_free_between_epochs_certified":False,
            "orders_submitted":0,
        })
        raise
