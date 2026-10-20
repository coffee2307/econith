"""Finite source-receipt -> AFTER durable checkpoint timestamp research proof.

Records bounded individual eligible events in memory, then seals a local JSON
observation list only after all original/receiver/observer/checkpoint ledgers
have reconciled. Samples are NOT independently clock attested. Crash before
seal leaves no accepted latency evidence. Never a 24h collection service.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

SCHEMA = "econith-p1-source-to-checkpoint-completion-v1"
PASS = "PASSED_LOCAL_CHECKPOINT_AGE_REAUDIT_ONLY"
MAX_ROWS = 5000
MAX_JSON_BYTES = 2_000_000


class CheckpointAgeError(ValueError):
    pass


def _bounded_age(value: object, cap: int) -> int:
    if type(value) is not int or value < 0 or value > cap:
        raise CheckpointAgeError("source receipt -> after checkpoint age exceeded")
    return value


def _body(rows: list[dict], budget_ms: int) -> dict:
    ages = sorted(x["checkpoint_completion_age_ms"] for x in rows)
    n = len(ages)
    if not n:
        raise CheckpointAgeError("no checkpoint ages to seal")
    return {
        "schema": SCHEMA,
        "budget_ms": budget_ms,
        "age_samples": n,
        "max_source_to_checkpoint_completion_ms": ages[-1],
        "p50_source_to_checkpoint_completion_ms": ages[(n - 1) // 2],
        "p95_source_to_checkpoint_completion_ms": ages[(95 * n - 1) // 100],
        "p99_source_to_checkpoint_completion_ms": ages[(99 * n - 1) // 100],
        "local_clock_independently_attested": False,
        "duration_24h_certified": False,
        "venue_completeness_verified": False,
        "orders_submitted": 0,
        "rows": rows,
    }


class BoundedCheckpointAgeProbe:
    def __init__(self, path: Path, *, budget_ms: int):
        if (type(budget_ms) is not int or not 100 <= budget_ms <= 5000
            or not isinstance(path, Path) or not path.is_absolute()
            or path.is_symlink() or path.exists()
            or path != path.resolve() or not path.parent.is_dir()):
            raise CheckpointAgeError("unsafe checkpoint age probe")
        self.path = path
        self.budget_ms = budget_ms
        self.rows: list[dict] = []

    def observe(self, envelope, receipt_id: str, checkpoint,
                *, observed_after_commit_ms: int | None = None) -> int:
        # Called ONLY after append_live has returned from a durable COMMIT.
        from infrastructure.market_cross_process_delivery import ProjectionEnvelope
        if type(envelope) is not ProjectionEnvelope:
            raise CheckpointAgeError("source projection must be a verified envelope")
        now = time.time_ns() // 1_000_000 if observed_after_commit_ms is None else observed_after_commit_ms
        if (type(now) is not int or
            type(checkpoint.checkpointed_events) is not int or
            checkpoint.checkpointed_events != len(self.rows) + 1 or
            checkpoint.last_capture_id != envelope.ingest_id or
            checkpoint.last_available_at_ms != envelope.available_at_ms or
            type(checkpoint.tail_sha256) is not str
            or len(checkpoint.tail_sha256) != 64
            or any(c not in "0123456789abcdef" for c in checkpoint.tail_sha256)
            or type(receipt_id) is not str or len(receipt_id) != 64
            or any(c not in "0123456789abcdef" for c in receipt_id)
            or len(self.rows) >= MAX_ROWS):
            raise CheckpointAgeError("nonsequential/missing durable checkpoint proof")
        elapsed = _bounded_age(now - envelope.available_at_ms, self.budget_ms)
        self.rows.append({
            "ordinal": checkpoint.checkpointed_events,
            "capture_id": envelope.ingest_id,
            "source_received_at_ms": envelope.available_at_ms,
            "observed_after_checkpoint_commit_ms": now,
            "checkpoint_completion_age_ms": elapsed,
            "receiver_receipt_id": receipt_id,
            "checkpoint_sha256": checkpoint.tail_sha256,
        })
        return elapsed

    def seal(self) -> dict:
        doc = _body(self.rows, self.budget_ms)
        raw = (json.dumps(doc, sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_JSON_BYTES:
            raise CheckpointAgeError("bounded checkpoint age samples oversized")
        with self.path.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return {k: v for k, v in doc.items() if k != "rows"}


def _read_sqlite(path: Path, statement: str):
    if (not path.is_file() or path.is_symlink()
        or path != path.resolve()):
        raise CheckpointAgeError("missing or aliased checkpoint source ledger")
    with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise CheckpointAgeError("source/receiver/checkpoint SQLite corruption")
        return [dict(x) for x in db.execute(statement).fetchall()]


def inspect(root: Path) -> dict:
    """Independent local recheck of timing record against actual durable chain.

    Clock samples are locally asserted and not authenticated by Binance/HMAC.
    An attacker controlling both source and report can fabricate time stamps.
    """
    if (not root.is_absolute() or root.is_symlink() or root != root.resolve()
        or not root.is_dir() or (root / ".forward_producer.lock").exists()):
        raise CheckpointAgeError("unsafe or active experiment root")
    path = root / "checkpoint_delivery_ages.json"
    if (path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES):
        raise CheckpointAgeError("missing/oversized private timing inventory")
    raw = path.read_bytes()
    if len(raw) == 0 or len(raw) > MAX_JSON_BYTES:
        raise CheckpointAgeError("incomplete timing bytes")
    doc = json.loads(raw)
    if (type(doc) is not dict or doc.get("schema") != SCHEMA
        or type(doc.get("budget_ms")) is not int
        or not 100 <= doc["budget_ms"] <= 5000
        or type(doc.get("rows")) is not list
        or not 1 <= len(doc["rows"]) <= MAX_ROWS):
        raise CheckpointAgeError("unsafe or incomplete checkpoint timing schema")
    rows = doc["rows"]
    if doc != _body(rows, doc["budget_ms"]):
        raise CheckpointAgeError("timing summary/clock claims disagree with samples")
    originals = _read_sqlite(
        root / "original.sqlite",
        "SELECT capture_id,received_at_ms FROM offline_spot_capture_records ORDER BY capture_id",
    )
    checkpoints = _read_sqlite(
        root / "forward_checkpoints.sqlite",
        "SELECT ordinal,capture_id,available_at_ms,receiver_receipt_id,checkpoint_sha256 "
        "FROM forward_checkpoint ORDER BY ordinal",
    )
    outbox = _read_sqlite(
        root / "forward_outbox.sqlite",
        "SELECT ingest_id,available_at_ms,receiver_receipt_id,status FROM forward_outbox ORDER BY ingest_id",
    )
    receiver = _read_sqlite(
        root / "independent_receiver.sqlite",
        "SELECT ingest_id,available_at_ms,receipt_id FROM offline_independent_projection_receipts ORDER BY ingest_id",
    )
    src = {row["capture_id"]: row["received_at_ms"] for row in originals}
    out = {row["ingest_id"]: row for row in outbox}
    recv = {row["ingest_id"]: row for row in receiver}
    if not (len(rows) == len(checkpoints) == len(outbox) == len(receiver)
            and len(src) == len(originals)
            and len(out) == len(outbox) and len(recv) == len(receiver)):
        raise CheckpointAgeError("checkpoint/receiver/outbox timing coverage missing")
    previous_wall = None
    for idx, (sample, cp) in enumerate(zip(rows, checkpoints), start=1):
        if type(sample) is not dict or set(sample) != {
            "ordinal", "capture_id", "source_received_at_ms",
            "observed_after_checkpoint_commit_ms", "checkpoint_completion_age_ms",
            "receiver_receipt_id", "checkpoint_sha256",
        }:
            raise CheckpointAgeError("untrusted timing sample")
        cid = sample["capture_id"]
        delivery = out.get(cid)
        ack = recv.get(cid)
        observed = sample["observed_after_checkpoint_commit_ms"]
        if (type(observed) is not int
            or type(cid) is not int
            or type(sample["ordinal"]) is not int
            or sample["ordinal"] != cp["ordinal"] or cp["ordinal"] != idx
            or sample["capture_id"] != cp["capture_id"]
            or sample["source_received_at_ms"] != cp["available_at_ms"]
            or sample["source_received_at_ms"] != src.get(cid)
            or sample["checkpoint_sha256"] != cp["checkpoint_sha256"]
            or sample["receiver_receipt_id"] != cp["receiver_receipt_id"]
            or delivery is None or ack is None
            or delivery["status"] != "DERIVED_ACKED"
            or delivery["receiver_receipt_id"] != ack["receipt_id"]
            or delivery["receiver_receipt_id"] != sample["receiver_receipt_id"]
            or delivery["available_at_ms"] != ack["available_at_ms"]
            or delivery["available_at_ms"] != sample["source_received_at_ms"]
            or sample["checkpoint_completion_age_ms"] != observed - cp["available_at_ms"]
            or (previous_wall is not None and observed < previous_wall)):
            raise CheckpointAgeError("timing source/receiver/ordered checkpoint mismatch")
        _bounded_age(sample["checkpoint_completion_age_ms"], doc["budget_ms"])
        previous_wall = observed
    return {
        "schema": SCHEMA,
        "verdict": PASS,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        "source_to_after_checkpoint_samples": len(rows),
        "max_source_to_after_checkpoint_ms": doc["max_source_to_checkpoint_completion_ms"],
        "p95_source_to_after_checkpoint_ms": doc["p95_source_to_checkpoint_completion_ms"],
        "p99_source_to_after_checkpoint_ms": doc["p99_source_to_checkpoint_completion_ms"],
        "budget_ms": doc["budget_ms"],
        "independent_local_ledger_binding_verified": True,
        "clock_samples_independently_attested": False,
        "source_venue_completeness_verified": False,
        "orders_submitted": 0,
    }
