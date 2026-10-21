"""P1-15 independent read-only host telemetry during one finite Spot/HMAC run.

Can observe an authorized pre-existing source on a VPS for a long period, but
NEVER declares a continuous 24h feed. This is separate from source-provenance
and HMAC cryptographic audit. No takeover, reconnection, orders or recovery.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable

from ci.phase1_cloud_resources import host_snapshot

SCHEMA = "econith-p1-host-watchdog-ledger-v1"
FINITE_PASS = "PASSED_FINITE_LOCAL_HOST_WATCH_ONLY_NOT_24H"
REFUSED = "REFUSED_OR_QUARANTINED_HOST_WATCH"
GENESIS = "0" * 64
MAX_OBSERVATIONS = 20000  # at 5-second cadence, enough for 24h
MAX_AGE_MS = 30000
MIN_MEM = 1_500_000_000
MIN_DISK = 2_000_000_000
MAX_EVIDENCE = 500_000_000


class HostWatchRefused(RuntimeError):
    pass


def _read_rows(path: Path, query: str) -> list[tuple] | None:
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink() or path != path.resolve():
        raise HostWatchRefused("aliased or nonfile research SQLite")
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            db.execute("PRAGMA query_only=ON")
            return db.execute(query).fetchall()
    except sqlite3.Error as exc:
        raise HostWatchRefused("research ledger unavailable or malformed") from exc


def sample(source: Path, *, now_ms: int | None = None,
           metrics: Callable[[Path], dict] = host_snapshot,
           max_age_ms: int = MAX_AGE_MS) -> dict:
    """Read-only, bounded host/source/receiver state. Never guesses unknown=0."""
    if (not isinstance(source, Path) or not source.is_absolute()
        or source != source.resolve() or source.is_symlink()
        or type(max_age_ms) is not int or not 1000 <= max_age_ms <= 60000):
        raise HostWatchRefused("unsafe research observation source")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if type(now) is not int or now <= 0:
        raise HostWatchRefused("invalid local host observation clock")
    if not source.exists():
        return {"state": "WAITING_FOR_SOURCE"}
    if not source.is_dir():
        raise HostWatchRefused("source is not a directory")
    physical = metrics(source)
    if (not isinstance(physical, dict)
        or any(type(physical.get(k)) is not int or physical[k] < 0
               for k in ("available_memory_bytes", "disk_free_bytes",
                         "evidence_file_bytes"))):
        raise HostWatchRefused("unavailable host resource telemetry")
    if (physical["available_memory_bytes"] < MIN_MEM
        or physical["disk_free_bytes"] < MIN_DISK
        or physical["evidence_file_bytes"] > MAX_EVIDENCE):
        raise HostWatchRefused("host memory/disk/evidence safety budget exceeded")
    marker = source / ".forward_producer.lock"
    if marker.is_symlink():
        raise HostWatchRefused("owner marker link refused")
    sessions = _read_rows(source / "original.sqlite", """
        SELECT session_id,opened_at_ms,closed_at_ms,source
        FROM offline_spot_capture_sessions
    """)
    if sessions is None or len(sessions) == 0:
        return {"state": "WAITING_FOR_SOURCE"}
    if len(sessions) != 1:
        raise HostWatchRefused("ambiguous source session")
    sid, started, closed, venue = sessions[0]
    if (not isinstance(sid, str) or not sid.startswith("rec_")
        or len(sid) > 48 or type(started) is not int or started <= 0
        or venue not in ("binance_public_simulation", "offline_fixture")
        or (closed is not None and
            (type(closed) is not int or closed < started))):
        raise HostWatchRefused("invalid source session identity")
    source_rows = _read_rows(source / "original.sqlite", """
        SELECT COUNT(*),MAX(capture_id),MAX(received_at_ms)
        FROM offline_spot_capture_records
    """)[0]
    intents = _read_rows(source / "original.sqlite", """
        SELECT COUNT(*),MAX(capture_id) FROM offline_forward_capture_intents
    """)[0]
    count, ordinal, last_ms = source_rows
    if (count != intents[0] or
        (count and (ordinal != count or intents[1] != count))
        or (not count and (ordinal is not None or intents[1] is not None))):
        raise HostWatchRefused("raw source vs atomic intent inventory diverged")
    if (count and (type(last_ms) is not int
                   or last_ms < started or last_ms > now + 1000)):
        raise HostWatchRefused("invalid source receive clock")
    open_source = closed is None
    if open_source and not marker.exists():
        raise HostWatchRefused("unclosed producer with no source owner fence")
    newest_age = now - (last_ms if count else started)
    if open_source and (newest_age < -1000 or newest_age > max_age_ms):
        raise HostWatchRefused("source receive progress stale or clock reversed")
    outbox = _read_rows(source / "forward_outbox.sqlite", """
        SELECT COUNT(*),
               COALESCE(SUM(CASE WHEN status='DERIVED_ACKED' THEN 1 ELSE 0 END),0)
        FROM forward_outbox
    """)
    receipts = _read_rows(source / "independent_receiver.sqlite", """
        SELECT COUNT(*) FROM offline_independent_projection_receipts
    """)
    checkpoints = _read_rows(source / "forward_checkpoints.sqlite", """
        SELECT COUNT(*) FROM forward_checkpoint
    """)
    cursor = _read_rows(source / "projection_cursor.sqlite", """
        SELECT COUNT(*),MAX(last_capture_id) FROM spot_projection_cursor
    """)
    if outbox is None or receipts is None or checkpoints is None or cursor is None:
        # The original may legitimately be created before auxiliary ledgers.
        if count > 0:
            raise HostWatchRefused("source committed data before a required ledger existed")
        return {"state": "INITIALIZING"}
    staged, derived = outbox[0]
    acked = receipts[0][0]
    cp = checkpoints[0][0]
    cursor_rows, projected = cursor[0]
    if (cursor_rows != 1 or type(projected) is not int
        or any(type(x) is not int or x < 0
               for x in (count, staged, derived, acked, cp))
        or not 0 <= cp <= derived <= staged <= count
        or acked > staged or cp > acked
        or not 0 <= projected <= count):
        raise HostWatchRefused("source, outbox, HMAC or checkpoint cursor mismatch")
    if not open_source and not marker.exists():
        if (projected != count or cp != acked or cp != derived
            or derived != staged):
            raise HostWatchRefused("closed source has incomplete HMAC/checkpoint suffix")
        state = "CLOSED_LOCALLY_COHERENT_NOT_24H"
    elif not open_source:
        state = "SOURCE_CLOSED_OWNER_TEARDOWN_PENDING"
    else:
        state = "SOURCE_OPEN"
    return {
        "state": state,
        "local_observed_ms": now,
        "source_identity_sha256": hashlib.sha256(sid.encode()).hexdigest(),
        "source_opened_ms": started,
        "source_closed_ms": closed,
        "source_owner_marker_present": marker.exists(),
        "source_originals": count,
        "source_intents": intents[0],
        "latest_source_age_ms": max(0, newest_age) if open_source else None,
        "staged_projections": staged,
        "derived_acked_projections": derived,
        "receiver_receipts": acked,
        "durable_checkpoints": cp,
        "source_projection_cursor": projected,
        "available_memory_bytes": physical["available_memory_bytes"],
        "disk_free_bytes": physical["disk_free_bytes"],
        "evidence_file_bytes": physical["evidence_file_bytes"],
        "source_gap_free_certified": False,
        "market_history_complete": False,
        "orders_submitted": 0,
    }


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def verify_trace(path: Path) -> dict:
    if (not isinstance(path, Path) or not path.is_absolute()
        or path.is_symlink() or not path.is_file()
        or path != path.resolve() or path.stat().st_size > 18_000_000):
        raise HostWatchRefused("missing, untrusted or oversized watchdog trace")
    prior = GENESIS
    count = 0
    active = 0
    last = None
    identity = None
    max_gap = 0
    with path.open("rb") as handle:
        for raw in handle:
            if len(raw) > 1500 or count >= MAX_OBSERVATIONS:
                raise HostWatchRefused("oversized watchdog observation")
            item = json.loads(raw)
            if type(item) is not dict or set(item) != {
                "ordinal", "previous_sha256", "sample", "sha256",
            } or item["ordinal"] != count + 1 or item["previous_sha256"] != prior:
                raise HostWatchRefused("watchdog ordinal/hash link invalid")
            body = {
                "ordinal": item["ordinal"], "previous_sha256": prior,
                "sample": item["sample"],
            }
            expected = hashlib.sha256(_canonical(body)).hexdigest()
            if expected != item["sha256"]:
                raise HostWatchRefused("watchdog sample tampered or reordered")
            point = item["sample"]
            if type(point) is not dict or point.get("state") not in (
                "WAITING_FOR_SOURCE", "INITIALIZING", "SOURCE_OPEN",
                "SOURCE_CLOSED_OWNER_TEARDOWN_PENDING",
                "CLOSED_LOCALLY_COHERENT_NOT_24H",
            ):
                raise HostWatchRefused("unknown observation state")
            if point["state"] == "SOURCE_OPEN":
                active += 1
            if "source_identity_sha256" in point:
                if identity is not None and identity != point["source_identity_sha256"]:
                    raise HostWatchRefused("source session switched inside one observation")
                identity = point["source_identity_sha256"]
                for field in ("source_originals", "source_intents",
                              "receiver_receipts", "durable_checkpoints",
                              "source_projection_cursor"):
                    if type(point.get(field)) is not int:
                        raise HostWatchRefused("missing source progress counter")
                    if last is not None and point[field] < last[field]:
                        raise HostWatchRefused("source/ACK/checkpoint progress rolled back")
                if last is not None:
                    gap = point["local_observed_ms"] - last["local_observed_ms"]
                    if type(gap) is not int or gap < 0 or gap > 60000:
                        raise HostWatchRefused("watchdog host clock jumped or observer stalled")
                    max_gap = max(max_gap, gap)
                last = point
            count += 1
            prior = expected
    if count == 0:
        raise HostWatchRefused("empty source observation")
    return {
        "trace_observations": count, "source_open_observations": active,
        "source_identity_sha256": identity, "tail_sha256": prior,
        "max_local_observation_gap_ms": max_gap,
        "local_clock_independently_attested": False,
        "continuous_market_history_proven": False,
    }


async def observe(source: Path, watch_dir: Path, *,
                  max_watch_s: float = 180,
                  poll_s: float = 1.,
                  startup_grace_s: float = 30.,
                  metrics: Callable[[Path], dict] = host_snapshot,
                  snapshot_fn=sample) -> dict:
    if (not isinstance(watch_dir, Path) or not watch_dir.is_absolute()
        or watch_dir.exists() or watch_dir.is_symlink()
        or watch_dir != watch_dir.resolve()
        or source == watch_dir or source in watch_dir.parents
        or not watch_dir.parent.is_dir()
        or not all(type(v) in (float, int) and math.isfinite(v)
                   for v in (max_watch_s, poll_s, startup_grace_s))
        or not 10 <= max_watch_s <= 90000
        or not .2 <= poll_s <= 60
        or not 5 <= startup_grace_s <= 60
        or int(max_watch_s / poll_s) > MAX_OBSERVATIONS):
        raise HostWatchRefused("unsafe bounded independent watchdog scope")
    watch_dir.mkdir(mode=0o700)
    trace = watch_dir / "observations.jsonl"
    start = time.monotonic()
    count = 0
    seen_open = False
    prev_hash = GENESIS
    latest = None
    reason = "WATCH_DURATION_EXPIRED_SOURCE_CONTINUITY_UNPROVEN"
    try:
        with trace.open("xb") as file:
            while time.monotonic() - start < max_watch_s:
                point = snapshot_fn(source, metrics=metrics)
                count += 1
                body = {"ordinal": count, "previous_sha256": prev_hash,
                        "sample": point}
                digest = hashlib.sha256(_canonical(body)).hexdigest()
                record = dict(body, sha256=digest)
                encoded = _canonical(record) + b"\n"
                if len(encoded) > 1500:
                    raise HostWatchRefused("watchdog sample oversized")
                file.write(encoded)
                file.flush()
                os.fsync(file.fileno())
                prev_hash = digest
                latest = point
                state = point["state"]
                if state == "SOURCE_OPEN":
                    seen_open = True
                if state in ("WAITING_FOR_SOURCE", "INITIALIZING") and (
                    time.monotonic() - start > startup_grace_s
                ):
                    raise HostWatchRefused("source did not start before watchdog grace")
                if state == "CLOSED_LOCALLY_COHERENT_NOT_24H":
                    if not seen_open:
                        raise HostWatchRefused("watchdog did not observe an active source")
                    reason = FINITE_PASS
                    break
                await asyncio.sleep(poll_s)
    except BaseException as exc:
        if not isinstance(exc, asyncio.CancelledError):
            reason = REFUSED
        else:
            reason = "WATCH_CANCELLED_SOURCE_NOT_CERTIFIED"
        raise
    finally:
        # A truncated/missing summary after os._exit is NOT a PASS.
        result = {
            "schema": SCHEMA, "verdict": reason,
            "observed_wall_s": round(time.monotonic() - start, 3),
            "samples": count, "source_was_active": seen_open,
            "latest_state": latest.get("state") if latest else None,
            "continuous_24h_qualified": False,
            "auto_restart_authorized": False,
            "owner_lock_reclamation_authorized": False,
            "orders_submitted": 0,
        }
        if trace.exists() and not (watch_dir / "watch_result.json").exists():
            with (watch_dir / "watch_result.json").open("xb") as file:
                file.write(_canonical(result) + b"\n")
                file.flush()
                os.fsync(file.fileno())
    check = verify_trace(trace)
    if check["source_open_observations"] == 0 or latest is None:
        raise HostWatchRefused("watchdog never witnessed source owner progress")
    return {**result, **check}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--observe-existing-research", action="store_true")
    p.add_argument("--acknowledge-not-24h-proof", action="store_true")
    p.add_argument("--source-dir", type=Path, required=True)
    p.add_argument("--watch-dir", type=Path, required=True)
    p.add_argument("--poll-s", type=float, default=5.)
    p.add_argument("--max-watch-s", type=float, default=86400.)
    a = p.parse_args(argv)
    if not (a.observe_existing_research and a.acknowledge_not_24h_proof):
        p.error("explicit read-only host observation and continuity limitation required")
    try:
        result = asyncio.run(observe(a.source_dir, a.watch_dir,
                                    max_watch_s=a.max_watch_s, poll_s=a.poll_s))
    except (ValueError, OSError, sqlite3.Error, TypeError,
            KeyError, RuntimeError) as exc:
        print(json.dumps({"verdict": REFUSED,
                          "failure_class": type(exc).__name__,
                          "continuous_24h_qualified": False,
                          "orders_submitted": 0}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["verdict"] == FINITE_PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
