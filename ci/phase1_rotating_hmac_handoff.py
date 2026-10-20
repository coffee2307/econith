"""Operator-only post-seal rotating Spot -> independent HMAC receiver handoff.

This OFFLINE gate consumes previously sealed raw SQLite journals; it NEVER
connects WebSockets or exchanges, and it does not claim continuous in-session
HMAC dispatch. Per journal, independently reconstruct source eligibility, stage
the exact projection, use a separately authenticated persistent receiver child,
deliver to three read-only derived observers and reconcile durable checkpoints.

The global source must be finished, all journals sealed, the source audit PASS,
and there must be no orphan owner marker. A true crash leaves an orphan owner
marker requiring operator review. There is no automatic replay after crash.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from core.mode import QuantMode
from ci.phase1_rotating_source_audit import inspect as inspect_source
from infrastructure.market_derived_research_receipts import OfflineDerivedResearchReceiptLedger
from infrastructure.market_named_research_subscribers import OfflineNamedResearchSubscriber
from infrastructure.spot_forward_checkpoints import OfflineForwardSessionCheckpoints
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_owner import exclusive_forward_owner
from infrastructure.spot_forward_recovery import recover_staged_forward_spot

_SCHEMA="econith-phase1-rotating-hmac-postseal-handoff-v1"

class HandoffRefused(RuntimeError):
    """No implied source, downstream or operator authorization."""

def _fsync_json(path: Path, data: dict) -> None:
    with path.open("x",encoding="utf-8") as f:
        json.dump(data,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())


async def handoff_one_sealed_segment(sd:Path, seal:dict, index:int)->dict:
    """Research-only sealed-segment handoff under a separately held GLOBAL owner.

    This method does NOT inspect the whole source root, claim global ownership,
    or authorize a new socket. Caller must already own root and have fsynced
    the source segment. Child recovery owns an additional O_EXCL segment fence.
    """
    if (sd.is_symlink() or not sd.is_dir() or (sd/".forward_producer.lock").exists()
        or type(index) is not int or index<0 or sd.name!=f"{index:04d}"
        or type(seal) is not dict or seal.get("index")!=index
        or not isinstance(seal.get("session_id"),str)
        or not seal["session_id"].startswith("rec_rot_")
        or (sd/"postseal_hmac_receipts.json").exists()):
        raise HandoffRefused("untrusted, unsealed or previously processed segment")
    sid=seal["session_id"]
    provider=lambda:QuantMode.SIMULATION
    for name in ("forward_outbox.sqlite","derived.sqlite","research_audit.sqlite",
                 "research_monitor.sqlite","forward_checkpoints.sqlite",
                 "independent_receiver.sqlite"):
        if (sd/name).exists():
            raise HandoffRefused("old downstream evidence requires operator review")
    # Verify complete originals before creating downstream ledgers.
    from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
    raw=OfflineSpotCaptureJournal(sd/"original.sqlite",mode_provider=provider,
                                  forward_capture_audit=True)
    try:
        items=raw.verify_forward_capture_inventory(sid,limit=1000)
        with raw._connect() as db:
            session=db.execute(
                "SELECT closed_at_ms FROM offline_spot_capture_sessions WHERE session_id=?",
                (sid,)).fetchone()
        if session is None or session["closed_at_ms"] is None or len(items)!=seal["original_events"]:
            raise HandoffRefused("unclosed or unexpected source original count")
    finally:
        raw.close()
    OfflineForwardSpotOutbox(
        sd/"forward_outbox.sqlite",session_id=sid,mode_provider=provider)
    OfflineDerivedResearchReceiptLedger(sd/"derived.sqlite",mode_provider=provider)
    for name in ("research_audit","research_monitor"):
        OfflineNamedResearchSubscriber(
            sd/(name+".sqlite"),name=name,
            expected_source="verified_forward_spot_shadow",
            mode_provider=provider,
        )
    OfflineForwardSessionCheckpoints(
        sd/"forward_checkpoints.sqlite",session_id=sid,mode_provider=provider)
    proof=await recover_staged_forward_spot(
        root=sd,session_id=sid,max_envelopes=1000,
        reconstruct_missing=True,max_book_staleness_ms=5000,
        persistent_receiver=True,
    )
    if (not proof.source_session_closed
        or not proof.original_eligibility_rescan_verified
        or proof.staged_envelopes<=0
        or proof.staged_envelopes!=proof.reconstructed_missing_intents
        or proof.staged_envelopes!=proof.receiver_receipts
        or proof.staged_envelopes!=proof.outbox_derived_acked
        or proof.staged_envelopes!=proof.forward_checkpoints
        or proof.primary_derived_receipts<=0
        or proof.independent_named_receipts!=2*proof.primary_derived_receipts
        or proof.session_certified or proof.source_coverage_certified):
        raise HandoffRefused("sealed segment receiver/observer/checkpoint ACK divergence")
    evidence={
        "index":index,"session_id":sid,"raw_original_events":seal["original_events"],
        "reconstructed_projections":proof.staged_envelopes,
        "receiver_receipts":proof.receiver_receipts,
        "primary_derived_receipts":proof.primary_derived_receipts,
        "independent_named_receipts":proof.independent_named_receipts,
        "forward_checkpoints":proof.forward_checkpoints,
        "original_eligibility_rescan_verified":True,
        "source_session_closed":True,"source_coverage_certified":False,
        "session_certified":False,
    }
    _fsync_json(sd/"postseal_hmac_receipts.json",evidence)
    return evidence


async def reconcile_sealed_rotation(root: Path, *,
                                    explicit_operator_approval:bool=False,
                                    inject_after_segments:int|None=None)->dict:
    if explicit_operator_approval is not True:
        raise PermissionError("explicit operator-only sealed research authorization required")
    if (not isinstance(root,Path) or not root.is_absolute()
        or root!=root.resolve() or root.is_symlink() or not root.is_dir()):
        raise HandoffRefused("unsafe sealed source root")
    report_path=root/"hmac_postseal_aggregate.json"
    if (report_path.exists() or (root/"hmac_postseal_quarantine.json").exists()
        or (root/".forward_producer.lock").exists()):
        raise HandoffRefused("prior handoff or owner marker requires manual review")
    source=inspect_source(root)
    if source["verdict"]!="PASSED_BOUNDED_LOCAL_SOURCE_ROTATION_AUDIT_ONLY":
        raise HandoffRefused("source-only rotation audit not accepted")
    manifests=json.loads((root/"aggregate_report.json").read_text(encoding="utf-8"))
    segments=manifests["segments"]
    if (not 2<=len(segments)<=32
        or any(not isinstance(x,dict) or not x["session_id"].startswith("rec_rot_")
               for x in segments)):
        raise HandoffRefused("only new eligible rec_ original sessions are admitted")
    result={"schema":_SCHEMA,"verdict":"INCOMPLETE_NOT_CERTIFIED",
            "mode":"SIMULATION","source_reader_attached_during_hmac":False,
            "postseal_handoff_only":True,"source_socket_count_observed":1,
            "sealed_segment_count":len(segments),"segments":[],"originals":0,
            "reconstructed_projections":0,"independent_receiver_receipts":0,
            "derived_receipts":0,"named_subscriber_receipts":0,
            "forward_checkpoints":0,"unobserved_venue_history_certified":False,
            "full_continuous_market_hmac_certified":False,
            "live_orders_authorized":False,"orders_submitted":0,
            "default_ingress_cutover":False,"institutional_phase1_complete":False}
    with exclusive_forward_owner(root,role="STAGED_RECOVERY"):
        try:
            for idx,seal in enumerate(segments):
                sd=root/"segments"/f"{idx:04d}"
                if sd.is_symlink() or not sd.is_dir() or (sd/".forward_producer.lock").exists():
                    raise HandoffRefused("segment is unsafe or owned")
                sid=seal["session_id"]
                provider=lambda:QuantMode.SIMULATION
                # Seed only new, private, empty downstream ledgers; recovery
                # will prove their association with this immutable source.
                for filename in ("forward_outbox.sqlite","derived.sqlite",
                                 "research_audit.sqlite","research_monitor.sqlite",
                                 "forward_checkpoints.sqlite"):
                    if (sd/filename).exists():
                        raise HandoffRefused("unreviewed old downstream ledger")
                OfflineForwardSpotOutbox(
                    sd/"forward_outbox.sqlite",session_id=sid,mode_provider=provider)
                OfflineDerivedResearchReceiptLedger(
                    sd/"derived.sqlite",mode_provider=provider)
                for name in ("research_audit","research_monitor"):
                    OfflineNamedResearchSubscriber(
                        sd/(name+".sqlite"),name=name,
                        expected_source="verified_forward_spot_shadow",
                        mode_provider=provider)
                OfflineForwardSessionCheckpoints(
                    sd/"forward_checkpoints.sqlite",
                    session_id=sid,mode_provider=provider)
                # Parent OS owner marker protects the entire operation.
                # The recovery routine acquires its own nested segment marker,
                # reads the sealed original, sends authenticated HMAC envelopes
                # to a different process and independently checkpoints them.
                item=await recover_staged_forward_spot(
                    root=sd,session_id=sid,
                    max_envelopes=1000,reconstruct_missing=True,
                    max_book_staleness_ms=5000,
                    persistent_receiver=True,
                )
                if (not item.source_session_closed
                    or not item.original_eligibility_rescan_verified
                    or item.staged_envelopes<=0
                    or item.staged_envelopes!=item.reconstructed_missing_intents
                    or item.staged_envelopes!=item.receiver_receipts
                    or item.staged_envelopes!=item.outbox_derived_acked
                    or item.staged_envelopes!=item.forward_checkpoints
                    or item.primary_derived_receipts<=0
                    or item.independent_named_receipts!=2*item.primary_derived_receipts
                    or item.session_certified or item.source_coverage_certified):
                    raise HandoffRefused("postseal HMAC receiver/observer/checkpoint parity failed")
                evidence={
                    "index":idx,"session_id":sid,
                    "raw_original_events":seal["original_events"],
                    "reconstructed_projections":item.staged_envelopes,
                    "receiver_receipts":item.receiver_receipts,
                    "primary_derived_receipts":item.primary_derived_receipts,
                    "independent_named_receipts":item.independent_named_receipts,
                    "forward_checkpoints":item.forward_checkpoints,
                    "original_eligibility_rescan_verified":True,
                    "source_session_closed":True,
                    "source_coverage_certified":False,
                    "session_certified":False,
                }
                _fsync_json(sd/"postseal_hmac_receipts.json",evidence)
                result["segments"].append(evidence)
                result["originals"]+=seal["original_events"]
                result["reconstructed_projections"]+=item.staged_envelopes
                result["independent_receiver_receipts"]+=item.receiver_receipts
                result["derived_receipts"]+=item.primary_derived_receipts
                result["named_subscriber_receipts"]+=item.independent_named_receipts
                result["forward_checkpoints"]+=item.forward_checkpoints
                if inject_after_segments is not None and idx+1>=inject_after_segments:
                    raise HandoffRefused("deliberately injected postseal interruption")
            if (len(result["segments"])!=len(segments)
                or result["originals"]!=source["raw_original_events"]
                or not result["reconstructed_projections"]
                or result["reconstructed_projections"]!=result["independent_receiver_receipts"]
                or result["reconstructed_projections"]!=result["forward_checkpoints"]
                or result["named_subscriber_receipts"]!=2*result["derived_receipts"]):
                raise HandoffRefused("end-to-end local postseal handoff parity failed")
            result["verdict"]="PASSED_BOUNDED_POSTSEAL_HMAC_HANDOFF_ONLY"
            _fsync_json(report_path,result)
        except BaseException as exc:
            # The source owner lock is removed by the standard context manager
            # only on graceful exception unwind. Failure marker prevents any
            # implicit/repeated handoff even after a graceful exception.
            _fsync_json(root/"hmac_postseal_quarantine.json",{
                "verdict":"HANDOFF_QUARANTINED_REQUIRES_OPERATOR_REVIEW",
                "failure_class":type(exc).__name__,
                "complete_segment_count":len(result["segments"]),
                "source_continuity_certified":False,
                "orders_submitted":0,
            })
            raise
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root",type=Path,required=True)
    p.add_argument("--allow-postseal-simulation-handoff",action="store_true")
    args=p.parse_args(argv)
    try:
        result=asyncio.run(reconcile_sealed_rotation(
            args.root,explicit_operator_approval=args.allow_postseal_simulation_handoff))
    except (Exception,) as exc:
        print(json.dumps({"verdict":"HANDOFF_REFUSED_OR_QUARANTINED",
                          "failure_class":type(exc).__name__,
                          "orders_submitted":0},sort_keys=True))
        return 2
    print(json.dumps({k:v for k,v in result.items() if k!="segments"},sort_keys=True))
    return 0

if __name__=="__main__":
    sys.exit(main())
