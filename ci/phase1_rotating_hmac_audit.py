"""Read-only cross-ledger audit of *completed post-seal* receiver HMAC handoff.

This auditor does not see HMAC session keys and cannot certify transport
cryptography on its own. It independently rehashes original -> immutable intent
-> outbox projection -> receiver-owned receipt identifiers -> checkpoint chain,
and reconciles per-segment subscriber count reports. All input SQLite opened
read-only; refuses incomplete or orphan-owner runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from ci.phase1_rotating_source_audit import inspect as inspect_source

def _read(dbfile:Path, query:str):
    if not dbfile.is_file() or dbfile.is_symlink() or dbfile!=dbfile.resolve():
        raise ValueError("unsafe downstream evidence file")
    with sqlite3.connect(f"file:{dbfile}?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise ValueError("downstream SQLite corruption")
        return [dict(x) for x in db.execute(query).fetchall()]

def _digest(prev:str,num:int,out:dict,receipt_id:str)->str:
    raw=json.dumps({
        "previous":prev,"ordinal":num,
        "session_event_id":out["event_id"],
        "capture_id":out["ingest_id"],
        "source_available_at_ms":out["available_at_ms"],
        "projection_fingerprint":out["projection_fingerprint"],
        "receiver_receipt_id":receipt_id,
    },sort_keys=True,separators=(",",":"),ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _audited_derived_rows(dbfile:Path, session_id:str)->list[dict]:
    """Verify *actual* durable derived observer rows, ordinal and receipt SHA.

    This does not establish EventBus-wide transactional delivery; it inspects
    only the three explicitly named protected research topics.
    """
    receipts=_read(dbfile,
        "SELECT * FROM offline_research_derived_receipts "
        "ORDER BY consumer_topic,ordinal")
    cursors=_read(dbfile,
        "SELECT * FROM offline_research_derived_cursors ORDER BY consumer_topic")
    topics={"md.ticker","indicator.volume_delta","indicator.obi"}
    totals={}
    for row in receipts:
        topic=row["consumer_topic"]
        if (row["session_id"]!=session_id or topic not in topics
            or type(row["ordinal"]) is not int
            or row["ordinal"]!=totals.get(topic,0)+1
            or not row["source_record_id"].startswith(session_id+":")):
            raise ValueError("derived research identity or ordinal corrupted")
        payload=json.loads(row["projection_json"])
        body=json.dumps(payload,sort_keys=True,separators=(",",":"),
                        ensure_ascii=False,allow_nan=False)
        digest=hashlib.sha256(body.encode("utf-8")).hexdigest()
        identity=json.dumps([
            session_id,topic,row["ordinal"],row["source_record_id"],
            row["source_available_at_ms"],digest,
        ],ensure_ascii=False,separators=(",",":"),allow_nan=False)
        expected=hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if (body!=row["projection_json"] or digest!=row["projection_digest"]
            or expected!=row["receipt_id"]):
            raise ValueError("derived receipt JSON/fingerprint/identity tampered")
        totals[topic]=row["ordinal"]
    cursor_map={row["consumer_topic"]:row["last_ordinal"] for row in cursors}
    if cursor_map!=totals or any(row["session_id"]!=session_id for row in cursors):
        raise ValueError("derived receipt cursor divergence")
    return receipts

def inspect(root:Path, *, co_run:bool=False)->dict:
    if not root.is_absolute() or root!=root.resolve() or root.is_symlink():
        raise ValueError("unsafe source root")
    if (root/".forward_producer.lock").exists() or (root/("hmac_corun_quarantine.json" if co_run else "hmac_postseal_quarantine.json")).exists():
        raise ValueError("handoff requires operator review")
    source=inspect_source(root)
    if source["verdict"]!="PASSED_BOUNDED_LOCAL_SOURCE_ROTATION_AUDIT_ONLY":
        raise ValueError("source journal rotation not independently verified")
    path=root/("hmac_corun_aggregate.json" if co_run else "hmac_postseal_aggregate.json")
    if path.is_symlink():
        raise ValueError("aliased HMAC handoff aggregate")
    report=json.loads(path.read_text(encoding="utf-8"))
    if (report.get("schema")!=("econith-phase1-rotating-hmac-corun-v1" if co_run else "econith-phase1-rotating-hmac-postseal-handoff-v1")
        or report.get("verdict")!=("PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY" if co_run else "PASSED_BOUNDED_POSTSEAL_HMAC_HANDOFF_ONLY")):
        raise ValueError("handoff did not finish normally")
    seg=report.get("segments")
    if type(seg) is not list or len(seg)!=source["original_journal_count"]:
        raise ValueError("incomplete cross-journal downstream inventory")
    originals=projections=received=checkpoints=derived=named=0
    tails=[]
    for index,one in enumerate(seg):
        sd=root/"segments"/f"{index:04d}"
        if sd.is_symlink() or (sd/".forward_producer.lock").exists():
            raise ValueError("untrusted or still-owned rotating segment")
        local=sd/"postseal_hmac_receipts.json"
        if not local.is_file() or local.is_symlink() or json.loads(local.read_text())!=one:
            raise ValueError("unsealed segment downstream report")
        sid=one["session_id"]
        src=_read(sd/"original.sqlite",
                  "SELECT capture_id,session_id,kind,received_at_ms,raw_blob,sha256 "
                  "FROM offline_spot_capture_records ORDER BY capture_id")
        intents=_read(sd/"original.sqlite",
                      "SELECT capture_id,session_id,kind,received_at_ms,sha256 "
                      "FROM offline_forward_capture_intents ORDER BY capture_id")
        out=_read(sd/"forward_outbox.sqlite",
                  "SELECT * FROM forward_outbox ORDER BY ingest_id")
        recv=_read(sd/"independent_receiver.sqlite",
                   "SELECT * FROM offline_independent_projection_receipts ORDER BY ingest_id")
        cp=_read(sd/"forward_checkpoints.sqlite",
                 "SELECT * FROM forward_checkpoint ORDER BY ordinal")
        if not(src and out and len(src)==len(intents)==one["raw_original_events"]
               and len(out)==len(recv)==len(cp)==one["reconstructed_projections"]
               ==one["receiver_receipts"]==one["forward_checkpoints"]):
            raise ValueError("one-segment original/receiver/checkpoint cardinality divergence")
        originals+=len(src)
        source_by_id={}
        for a,b in zip(src,intents):
            if (a["capture_id"]!=b["capture_id"]
                or a["session_id"]!=b["session_id"] or a["session_id"]!=sid
                or a["sha256"]!=b["sha256"]
                or a["kind"]!=b["kind"]
                or a["received_at_ms"]!=b["received_at_ms"]
                or hashlib.sha256(a["raw_blob"]).hexdigest()!=a["sha256"]):
                raise ValueError("source raw/intent evidence mismatch")
            source_by_id[a["capture_id"]]=a
        prev="0"*64
        for ordinal,(o,r,k) in enumerate(zip(out,recv,cp),1):
            original=source_by_id.get(o["ingest_id"])
            if original is None or o["status"]!="DERIVED_ACKED":
                raise ValueError("unreferenced/not acknowledged source projection")
            item=json.loads(o["projection_json"])
            canonical=json.dumps(item,sort_keys=True,separators=(",",":"),ensure_ascii=False)
            if (canonical!=o["projection_json"]
                or hashlib.sha256(canonical.encode()).hexdigest()!=o["projection_fingerprint"]
                or item["source_raw_sha256"]!=original["sha256"]
                or item["source_capture_id"]!=o["ingest_id"]
                or item["source_available_at_ms"]!=original["received_at_ms"]):
                raise ValueError("projection fingerprint/source identity mismatch")
            rx_raw=json.dumps(
                [o["source_consumer"],o["receiver"],o["ingest_id"],o["event_id"],
                 o["available_at_ms"],o["projection_fingerprint"]],
                separators=(",",":"),ensure_ascii=False,allow_nan=False)
            receipt=hashlib.sha256(rx_raw.encode()).hexdigest()
            if (r["ingest_id"]!=o["ingest_id"]
                or r["source_consumer"]!="forward_spot"
                or r["receiver"]!="read_only_market"
                or r["event_id"]!=o["event_id"]
                or r["projection_json"]!=o["projection_json"]
                or r["projection_fingerprint"]!=o["projection_fingerprint"]
                or r["available_at_ms"]!=o["available_at_ms"]
                or r["receipt_id"]!=receipt or o["receiver_receipt_id"]!=receipt
                or k["capture_id"]!=o["ingest_id"]
                or k["source_event_id"]!=o["event_id"]
                or k["available_at_ms"]!=o["available_at_ms"]
                or k["projection_fingerprint"]!=o["projection_fingerprint"]
                or k["receiver_receipt_id"]!=receipt
                or k["ordinal"]!=ordinal
                or k["previous_sha256"]!=prev
                or k["checkpoint_sha256"]!=_digest(prev,ordinal,o,receipt)):
                raise ValueError("independent receiver/checkpoint chain diverged")
            prev=k["checkpoint_sha256"]
        primary=_audited_derived_rows(sd/"derived.sqlite",sid)
        if len(primary)!=one["primary_derived_receipts"] or not primary:
            raise ValueError("primary derived receipts missing or unverified")
        for name in ("research_audit","research_monitor"):
            ledger=sd/(name+".sqlite")
            names=_read(ledger,"SELECT name,expected_source FROM offline_named_research_identity")
            if names!=[{"name":name,"expected_source":"verified_forward_spot_shadow"}]:
                raise ValueError("independent named subscriber identity mismatch")
            independently_saved=_audited_derived_rows(ledger,sid)
            if independently_saved!=primary:
                raise ValueError("named subscriber diverges from primary derived ledger")
        if one["independent_named_receipts"]!=2*len(primary):
            raise ValueError("named subscriber durable receipt count divergence")
        projections+=len(out)
        received+=len(recv)
        checkpoints+=len(cp)
        derived+=len(primary)
        named+=2*len(primary)
        tails.append(prev)
        if (one["index"]!=index or not one["original_eligibility_rescan_verified"]
            or not one["source_session_closed"]
            or one["session_certified"] or one["source_coverage_certified"]):
            raise ValueError("downstream report falsely claims source certification")
    if not(all([
        originals==source["raw_original_events"]==report["originals"],
        projections==received==checkpoints==report["reconstructed_projections"]
          ==report["independent_receiver_receipts"]==report["forward_checkpoints"],
        derived==report["derived_receipts"],
        named==report["named_subscriber_receipts"]==2*derived,
        report.get("source_reader_attached_during_hmac") is co_run,
        report.get("postseal_handoff_only") is (not co_run),
        (not co_run or
          (report.get("per_segment_postseal_delivery") is True
           and 1<=report.get("concurrent_segments_completed",0)<=len(seg)
           and 1<=report.get("segment_backlog_peak",3)<=2
           and report.get("segment_queue_capacity")==2
           and 0<report.get("segment_handoff_lag_max_ms",999999)<=30000
           and report.get("sealed_segment_count")==len(seg))),

        report.get("HMAC_downstream_tested") is None,
        report.get("unobserved_venue_history_certified") is False,
        report.get("full_continuous_market_hmac_certified") is False,
        report.get("live_orders_authorized") is False,
        report.get("orders_submitted")==0,
        report.get("default_ingress_cutover") is False,
        report.get("institutional_phase1_complete") is False,
    ])):
        raise ValueError("global sealed handoff evidence incomplete")
    return {
        "verdict":("PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY" if co_run
                   else "PASSED_LOCAL_POSTSEAL_RECEIVER_CHECKPOINT_CHAIN_ONLY"),
        "segments":len(seg),"raw_original_events":originals,
        "source_projections":projections,"receiver_owned_receipts":received,
        "forward_checkpoints":checkpoints,
        "primary_derived_receipts_reported":derived,
        "named_subscriber_receipts_reported":named,
        "all_checkpoint_chain_hashes_verified":True,
        "HMAC_IPC_channel_independently_reattested":False,
        "continuous_live_hmac_verified":False,
        "exchange_history_completeness_verified":False,
        "orders_submitted":0,
    }

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,required=True)
    a=parser.parse_args(argv)
    try:r=inspect(a.root)
    except (OSError,sqlite3.Error,TypeError,KeyError,ValueError,IndexError) as exc:
        r={"verdict":"POSTSEAL_AUDIT_REFUSED","failure_class":type(exc).__name__,
           "orders_submitted":0}
    print(json.dumps(r,sort_keys=True))
    return 0 if r["verdict"]=="PASSED_LOCAL_POSTSEAL_RECEIVER_CHECKPOINT_CHAIN_ONLY" else 2

if __name__=="__main__":
    raise SystemExit(main())
