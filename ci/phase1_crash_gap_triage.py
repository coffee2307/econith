"""Phase 1 P1-14: read-only, fail-closed original/ACK/checkpoint crash triage.

No lock removal, source restart, replay, repair, network, orders or automatic
recovery authorization. A live process cannot be ruled out by file contents.
The output is a forensic inventory, never an accepted continuous market feed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope,
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.spot_forward_checkpoints import _digest, _GENESIS

SCHEMA = "econith-p1-crash-local-ledger-triage-v1"
MAX_RECORDS = 5000
MAX_BODY_BYTES = 262144


class CrashTriageRefused(ValueError):
    pass


def _rows(path: Path, queries: list[str]) -> list[list[dict]]:
    if (not path.is_absolute() or not path.is_file() or path.is_symlink()
        or path != path.resolve()):
        raise CrashTriageRefused("missing/aliased evidence ledger")
    # Every ledger is opened independently read-only. No schema creation,
    # WAL checkpointing, repair or implicit transaction is performed.
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise CrashTriageRefused("original ledger integrity check failed")
            tables = [[dict(x) for x in db.execute(query).fetchall()]
                      for query in queries]
    except sqlite3.Error as exc:
        raise CrashTriageRefused("source or receiver schema/ledger unavailable") from exc
    if any(len(rows) > MAX_RECORDS for rows in tables):
        raise CrashTriageRefused("finite research ledger bound exceeded")
    return tables


def inspect(root: Path) -> dict:
    if (not isinstance(root, Path) or not root.is_absolute()
        or not root.is_dir() or root.is_symlink()
        or root != root.resolve()):
        raise CrashTriageRefused("absolute, unaliased isolated evidence required")
    owner = root / ".forward_producer.lock"
    if owner.is_symlink():
        raise CrashTriageRefused("owner fence was replaced by a symlink")
    owner_present = owner.exists()
    sessions, originals, intents = _rows(
        root / "original.sqlite", [
            "SELECT * FROM offline_spot_capture_sessions",
            "SELECT * FROM offline_spot_capture_records ORDER BY capture_id",
            "SELECT * FROM offline_forward_capture_intents ORDER BY capture_id",
        ],
    )
    if (len(sessions) != 1 or not originals or len(originals) != len(intents)
        or len(originals) > MAX_RECORDS):
        raise CrashTriageRefused("source/atomic intent inventory incomplete")
    sid = sessions[0]["session_id"]
    if (not isinstance(sid, str) or not sid.startswith("rec_")
        or len(sid) > 48 or sessions[0]["source"] not in (
            "offline_fixture", "binance_public_simulation"
        )):
        raise CrashTriageRefused("unrecognized research session identity")
    closed_at = sessions[0]["closed_at_ms"]
    prior_ms = None
    raw_by_id = {}
    for ordinal, (rec, intent) in enumerate(zip(originals, intents), start=1):
        raw = rec["raw_blob"]
        ms = rec["received_at_ms"]
        if (rec["capture_id"] != ordinal
            or intent["capture_id"] != ordinal
            or rec["session_id"] != sid or intent["session_id"] != sid
            or rec["kind"] != intent["kind"]
            or type(ms) is not int or ms <= 0
            or ms != intent["received_at_ms"]
            or (prior_ms is not None and ms < prior_ms)
            or not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY_BYTES
            or hashlib.sha256(raw).hexdigest() != rec["sha256"]
            or rec["sha256"] != intent["sha256"]):
            raise CrashTriageRefused("source/intent digest, ordering or clock diverged")
        prior_ms = ms
        raw_by_id[ordinal] = rec
    if closed_at is not None and (
        type(closed_at) is not int or closed_at < originals[-1]["received_at_ms"]
    ):
        raise CrashTriageRefused("source termination clock inconsistent")
    identity, staged = _rows(
        root / "forward_outbox.sqlite", [
            "SELECT session_id FROM forward_outbox_identity",
            "SELECT * FROM forward_outbox ORDER BY ingest_id",
        ],
    )
    if len(identity) != 1 or identity[0]["session_id"] != sid:
        raise CrashTriageRefused("outbox belongs to a different source session")
    receipts, = _rows(
        root / "independent_receiver.sqlite", [
            "SELECT * FROM offline_independent_projection_receipts ORDER BY ingest_id",
        ],
    )
    checkpoints, = _rows(
        root / "forward_checkpoints.sqlite", [
            "SELECT * FROM forward_checkpoint ORDER BY ordinal",
        ],
    )
    validated = {}
    staged_status = {"STAGED": 0, "RECEIVER_ACKED": 0, "DERIVED_ACKED": 0}
    for item in staged:
        cid = item["ingest_id"]
        original = raw_by_id.get(cid)
        if (type(cid) is not int or cid in validated or original is None
            or item["source_consumer"] != "forward_spot"
            or item["receiver"] != "read_only_market"
            or item["event_id"] != f"md:forward:{sid}:{cid}"
            or item["available_at_ms"] != original["received_at_ms"]
            or item["status"] not in staged_status
            or not isinstance(item["projection_json"], str)
            or len(item["projection_json"].encode()) > 65536):
            raise CrashTriageRefused("outbox identity/availability invalid")
        try:
            body = json.loads(item["projection_json"])
            canon, digest = OfflineMarketProjectionCursor._serialize(body)
        except (TypeError, ValueError) as exc:
            raise CrashTriageRefused("outbox original projection invalid") from exc
        if (canon != item["projection_json"]
            or digest != item["projection_fingerprint"]
            or type(body) is not dict
            or body.get("session_id") != sid
            or body.get("source_capture_id") != cid
            or body.get("source_available_at_ms") != original["received_at_ms"]
            or body.get("source_raw_sha256") != original["sha256"]
            or body.get("topic") not in ("md.aggTrade", "md.depth")):
            raise CrashTriageRefused("outbox original SHA/projection mismatch")
        envelope = ProjectionEnvelope(
            item["source_consumer"], item["receiver"], cid,
            item["event_id"], item["available_at_ms"],
            item["projection_json"], item["projection_fingerprint"],
        )
        expected_receipt = OfflineIndependentProjectionReceiver._receipt_id(envelope)
        if item["receiver_receipt_id"] not in (None, expected_receipt):
            raise CrashTriageRefused("outbox receiver receipt identity diverged")
        if item["status"] != "STAGED" and item["receiver_receipt_id"] is None:
            raise CrashTriageRefused("outbox ACK status with no receipt")
        staged_status[item["status"]] += 1
        validated[cid] = (item, envelope, expected_receipt)
    acknowledged = {}
    for row in receipts:
        cid = row["ingest_id"]
        bundle = validated.get(cid)
        if (cid in acknowledged or bundle is None
            or row["source_consumer"] != "forward_spot"
            or row["receiver"] != "read_only_market"
            or row["event_id"] != bundle[1].event_id
            or row["available_at_ms"] != bundle[1].available_at_ms
            or row["projection_json"] != bundle[1].projection_json
            or row["projection_fingerprint"] != bundle[1].projection_fingerprint
            or row["receipt_id"] != bundle[2]
            or type(row["received_at_ms"]) is not int
            or row["received_at_ms"] < row["available_at_ms"]):
            raise CrashTriageRefused("independent receiver-owned receipt mismatch")
        acknowledged[cid] = row
    if any(row["status"] != "STAGED" and cid not in acknowledged
           for cid, (row, _, _) in validated.items()):
        raise CrashTriageRefused("ACKed outbox record has no independently owned ACK")
    # A sender can die AFTER the child committed, BEFORE updating its own
    # outbox: an independently existing receipt with STAGED is legitimate.
    prev = _GENESIS
    if len(checkpoints) > len(staged):
        raise CrashTriageRefused("checkpoint length exceeds original outbox")
    for index, cp in enumerate(checkpoints, start=1):
        if (index > len(staged)
            or cp["ordinal"] != index
            or cp["previous_sha256"] != prev):
            raise CrashTriageRefused("checkpoint ordinal/chain prefix corrupt")
        item = staged[index-1]
        cid = item["ingest_id"]
        record, env, expected = validated[cid]
        if (record["status"] != "DERIVED_ACKED" or cid not in acknowledged
            or cp["capture_id"] != cid
            or cp["source_event_id"] != env.event_id
            or cp["available_at_ms"] != env.available_at_ms
            or cp["projection_fingerprint"] != env.projection_fingerprint
            or cp["receiver_receipt_id"] != expected
            or cp["checkpoint_sha256"] != _digest(prev, index, env, expected)):
            raise CrashTriageRefused("checkpoint not exact durable derived-ACK prefix")
        prev = cp["checkpoint_sha256"]
    # This is a conservative snapshot; mutation can occur after read if a
    # different process is active. The owner marker is never stolen.
    prefix_gap = staged_status["DERIVED_ACKED"] - len(checkpoints)
    incomplete = bool(
        owner_present or closed_at is None or prefix_gap
        or any(row["status"] != "DERIVED_ACKED" for row in staged)
        or len(receipts) != len(staged)
    )
    return {
        "schema": SCHEMA,
        "verdict": ("QUARANTINED_MANUAL_OWNER_AND_RECOVERY_REVIEW"
                    if incomplete else "CLOSED_LOCAL_LEDGER_PREFIX_CONSISTENT_ONLY"),
        "source_session_closed": closed_at is not None,
        "owner_fence_present": owner_present,
        "owner_liveness_verified": False,
        "original_and_atomic_intent_rows": len(originals),
        "last_original_capture_id": len(originals),
        "outbox_envelopes": len(staged),
        "outbox_staged_only": staged_status["STAGED"],
        "outbox_receiver_acked": staged_status["RECEIVER_ACKED"],
        "outbox_derived_acked": staged_status["DERIVED_ACKED"],
        "independent_receiver_receipts": len(receipts),
        "receiver_receipts_ahead_of_outbox_ack": sum(
            cid in acknowledged and row["status"] == "STAGED"
            for cid, (row, _, _) in validated.items()
        ),
        "durable_checkpoints": len(checkpoints),
        "derived_acked_but_not_checkpointed": prefix_gap,
        "raw_originals_not_staged_not_equivalent_to_missing_eligible": (
            len(originals) - len(staged)
        ),
        "checkpoint_tail_sha256": prev,
        "source_session_continuity_certified": False,
        "unknown_market_history_during_recovery": True,
        "restored_market_feed_authorized": False,
        "automatic_lock_reclamation_authorized": False,
        "mutation_performed": False,
        "real_orders_submitted": 0,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--trial-dir", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        result = inspect(a.trial_dir)
    except (CrashTriageRefused, OSError, sqlite3.Error, ValueError,
            TypeError, KeyError, IndexError) as exc:
        result = {
            "schema": SCHEMA,
            "verdict": "FORENSIC_REFUSED_UNTRUSTED_LOCAL_EVIDENCE",
            "error_class": type(exc).__name__,
            "mutation_performed": False,
            "source_session_continuity_certified": False,
            "automatic_lock_reclamation_authorized": False,
            "real_orders_submitted": 0,
        }
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["verdict"] == "CLOSED_LOCAL_LEDGER_PREFIX_CONSISTENT_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
