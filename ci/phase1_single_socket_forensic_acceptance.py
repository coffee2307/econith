"""Strict independent acceptance of one-socket Spot source originals.

This builds on the existing SQLite/SHA cardinality auditor and reopens the
private original journal to check record semantics, identities, ordered local
timestamps and the exact report/session tie. SHA equality alone cannot prove
that captured bytes came from Binance or that unseen events never existed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3

from ci.phase1_single_socket_duration_audit import inspect as inspect_basic
from infrastructure.spot_public_capture_adapter import _PUBLIC_WS, _PUBLIC_REST

PASS = "PASSED_SINGLE_SOCKET_LOCAL_ORIGINAL_SEMANTICS_ONLY"


def inspect(root: Path) -> dict:
    if (not isinstance(root, Path) or not root.is_absolute()
        or root.is_symlink() or root != root.resolve() or not root.is_dir()):
        raise ValueError("absolute unaliased private original root required")
    prior = inspect_basic(root)
    if prior["verdict"] != "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY":
        raise ValueError("existing independent source journal acceptance refused")
    report_path = root / "aggregate_report.json"
    database_path = root / "original.sqlite"
    if (report_path.is_symlink() or database_path.is_symlink()
        or (root / ".forward_producer.lock").exists()):
        raise ValueError("untrusted or active original evidence directory")
    raw_report = report_path.read_bytes()
    report = json.loads(raw_report)
    with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite original integrity check failed")
        sessions = db.execute(
            "SELECT * FROM offline_spot_capture_sessions"
        ).fetchall()
        rows = db.execute(
            "SELECT * FROM offline_spot_capture_records ORDER BY capture_id"
        ).fetchall()
        intents = db.execute(
            "SELECT * FROM offline_forward_capture_intents ORDER BY capture_id"
        ).fetchall()
    if len(sessions) != 1 or not rows or len(rows) != len(intents):
        raise ValueError("missing unique source session or capture-intent pairing")
    session = sessions[0]
    if (type(session["session_id"]) is not str
        or not session["session_id"].startswith("sds_")
        or session["session_id"] != report.get("session_id")
        or session["source"] != "binance_public_simulation"
        or session["symbol"] != "BTCUSDT"
        or session["ws_endpoint"] != _PUBLIC_WS
        or type(session["opened_at_ms"]) is not int
        or type(session["closed_at_ms"]) is not int
        or not 0 < session["opened_at_ms"] <= session["closed_at_ms"]
        or report.get("source") != "binance_public_data_only"):
        raise ValueError("source session identity, transport or lifecycle conflict")
    counts = {"aggTrade": 0, "depthUpdate": 0, "snapshot": 0}
    prior_ms = session["opened_at_ms"]
    first_ms = None
    last_ws_ms = None
    max_ws_gap_ms = 0
    for index, (record, intent) in enumerate(zip(rows, intents), start=1):
        ms = record["received_at_ms"]
        body_bytes = record["raw_blob"]
        if (record["capture_id"] != index
            or intent["capture_id"] != index
            or record["session_id"] != session["session_id"]
            or intent["session_id"] != record["session_id"]
            or type(ms) is not int
            or not prior_ms <= ms <= session["closed_at_ms"]
            or intent["received_at_ms"] != ms
            or record["kind"] not in counts
            or intent["kind"] != record["kind"]
            or not isinstance(body_bytes, bytes)
            or not 0 < len(body_bytes) <= 262144
            or hashlib.sha256(body_bytes).hexdigest() != record["sha256"]
            or intent["sha256"] != record["sha256"]):
            raise ValueError("original ordinal, receipt time, SHA or intent diverged")
        try:
            payload = json.loads(body_bytes)
        except (UnicodeDecodeError, TypeError, ValueError) as exc:
            raise ValueError("invalid JSON original source") from exc
        if type(payload) is not dict:
            raise ValueError("source payload object required")
        kind = record["kind"]
        if kind == "snapshot":
            if (record["endpoint"] != _PUBLIC_REST
                or record["request_symbol"] != "BTCUSDT"
                or type(record["request_started_at_ms"]) is not int
                or not session["opened_at_ms"] <= record["request_started_at_ms"] <= ms
                or type(payload.get("lastUpdateId")) is not int
                or payload["lastUpdateId"] <= 0
                or type(payload.get("bids")) is not list
                or type(payload.get("asks")) is not list):
                raise ValueError("REST snapshot endpoint, symbol, clock or schema refused")
        else:
            streams = (("btcusdt@aggTrade",) if kind == "aggTrade"
                       else ("btcusdt@depth", "btcusdt@depth@100ms"))
            data = payload.get("data")
            if (record["endpoint"] != _PUBLIC_WS
                or record["request_symbol"] is not None
                or record["request_started_at_ms"] is not None
                or payload.get("stream") not in streams
                or type(data) is not dict
                or data.get("s") != "BTCUSDT" or data.get("e") != kind):
                raise ValueError("WebSocket original stream identity refused")
            if kind == "depthUpdate" and (
                type(data.get("U")) is not int
                or type(data.get("u")) is not int
                or data["U"] <= 0 or data["u"] < data["U"]
            ):
                raise ValueError("malformed depth sequence")
            if last_ws_ms is not None:
                max_ws_gap_ms = max(max_ws_gap_ms, ms - last_ws_ms)
            last_ws_ms = ms
        if first_ms is None:
            first_ms = ms
        prior_ms = ms
        counts[kind] += 1
    frames = counts["aggTrade"] + counts["depthUpdate"]
    if (session["last_receipt_ms"] != prior_ms
        or report.get("websocket_frames") != frames
        or report.get("read_frames") != frames
        or report.get("snapshot_captures") != counts["snapshot"]
        or counts["snapshot"] != 1
        or report.get("duration_target_reached") is not True
        or report.get("single_socket_session_started") is not True
        or report.get("frame_cap") != 4998
        or report.get("queue_capacity") != 128
        or report.get("source_event_losslessness_certified") is not False
        or report.get("hmac_forward_receiver_tested") is not False
        or report.get("default_runtime_cutover") is not False
        or report.get("institutional_phase1_complete") is not False
        or report.get("orders_submitted") != 0):
        raise ValueError("original session cursor or safety report is inconsistent")
    return {
        "schema": "econith-p1-single-socket-local-semantic-audit-v1",
        "verdict": PASS,
        "source_report_sha256": hashlib.sha256(raw_report).hexdigest(),
        "source_sessions": 1,
        "originals": len(rows), "capture_intents": len(intents),
        "websocket_frames": frames, "rest_snapshots": 1,
        "first_original_received_ms": first_ms,
        "last_original_received_ms": prior_ms,
        "maximum_observed_ws_receipt_gap_ms": max_ws_gap_ms,
        "original_payloads_semantically_checked": len(rows),
        "original_source_digest_mismatches": 0,
        "real_exchange_history_completeness_proven": False,
        "continuous_hmac_delivery_proven": False,
        "institutional_phase1_complete": False,
        "default_runtime_cutover": False,
        "orders_submitted": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.root)
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        result = {"verdict": "AUDIT_REFUSED",
                  "failure_class": type(exc).__name__, "orders_submitted": 0}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
