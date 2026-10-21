"""Finite, foreground, unattended-safe SIMULATION-only paper supervisor.

Public Freqtrade/Hummingbot-inspired ops: exclusive owner fence, resource
budgets, fail-closed controller, monotonic clocks, hash-chain evidence and
replayable end report. This is NOT a daemon, investment strategy or
venue-attested 24h history. No keys, order methods, or real capital.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid
from typing import Callable

from econith.quant.forward_quote_dryrun import PaperRefused, status
from econith.quant.paper_forward_monitor import report
from scripts.paper_forward_cycle import cycle

SCHEMA = "econith-paper-supervisor-finite-v1"
GENESIS = "0" * 64
PASS = "FINITE_FOREGROUND_PAPER_SUPERVISION_COMPLETE_NOT_24H_PROOF"
FAIL = "RESEARCH_HALTED_INCOMPLETE_NO_CAPITAL_READY"
_ALLOWED_CYCLE = {"POST_SIGNAL_PAPER_CYCLE_RECONCILED",
                  "MARKED_NO_NEW_CLOSED_BAR"}
PERIOD_MS = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}


def _canonical_bytes(x: dict) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _digest(x: dict) -> str:
    return hashlib.sha256(_canonical_bytes(x)).hexdigest()


class Evidence:
    """Append + fsync one event, then independently audit EVERY record."""
    def __init__(self, root: Path) -> None:
        if (not root.is_absolute() or root != root.resolve()
            or root.exists() or root.is_symlink() or not root.parent.is_dir()
            or root.parent.is_symlink()):
            raise PaperRefused("new absolute private, unaliased evidence root required")
        root.mkdir(mode=0o700)
        self.root = root
        self.path = root / "events.jsonl"
        self.fd = self.path.open("xb")
        self.seq = 0
        self.prev = GENESIS

    def append(self, kind: str, at_ms: int, attrs: dict) -> None:
        self.seq += 1
        obj = {"schema": SCHEMA, "seq": self.seq, "prev_sha256": self.prev,
               "kind": kind, "local_ms": at_ms, "attributes": attrs}
        obj["sha256"] = _digest(obj)
        self.fd.write(_canonical_bytes(obj) + b"\n")
        self.fd.flush()
        os.fsync(self.fd.fileno())
        self.prev = obj["sha256"]

    def close(self) -> None:
        if not self.fd.closed:
            self.fd.close()


def audit_events(path: Path) -> dict:
    seq, previous, old_ms = 0, GENESIS, 0
    steps, complete, faults = 0, 0, 0
    types: list[str] = []
    with path.open("rb") as f:
        for line in f:
            v = json.loads(line)
            payload = {k: val for k, val in v.items() if k != "sha256"}
            if (v.get("schema") != SCHEMA or set(v) !=
                {"schema", "seq", "prev_sha256", "kind", "local_ms",
                 "attributes", "sha256"}
                or v["seq"] != seq + 1 or v["prev_sha256"] != previous
                or v["sha256"] != _digest(payload)
                or type(v["local_ms"]) is not int or v["local_ms"] < old_ms
                or type(v["attributes"]) is not dict):
                raise PaperRefused("supervisor journal hash/sequence/time invalid")
            kind = v["kind"]
            if kind not in ("START", "STEP", "HALT", "STOP"):
                raise PaperRefused("invalid supervisor event type")
            if kind == "STEP":
                a = v["attributes"]
                if (a.get("cycle_status") not in _ALLOWED_CYCLE
                    or a.get("real_orders") != 0
                    or type(a.get("quote_marks")) is not int
                    or a["quote_marks"] < 0):
                    raise PaperRefused("supervisor step attestation invalid")
                steps += 1
            if kind == "STOP":
                complete += 1
            if kind == "HALT":
                faults += 1
            types.append(kind)
            seq += 1
            previous = v["sha256"]
            old_ms = v["local_ms"]
    if (not seq or types[0] != "START" or types[-1] not in ("HALT", "STOP")
        or complete + faults != 1 or seq != steps + 2):
        raise PaperRefused("supervisor journal missing unique terminal state")
    return {"replayed_events": seq, "replayed_steps": steps,
            "terminator": types[-1], "tail_sha256": previous,
            "integrity": "INDEPENDENT_LOCAL_SHA256_CHAIN_REAUDITED",
            "externally_attested": False}


def _resource_guard(ledger: Path, min_free_mb: int,
                    max_ledger_mb: int) -> dict:
    if (not ledger.is_absolute() or not ledger.is_file()
        or ledger.is_symlink() or ledger != ledger.resolve()):
        raise PaperRefused("research ledger must already exist at an absolute path")
    disk = shutil.disk_usage(ledger.parent)
    total = (ledger.stat().st_size +
             sum((ledger.parent / (ledger.name + s)).stat().st_size
                 for s in ("-wal", "-shm")
                 if (ledger.parent / (ledger.name + s)).exists()))
    if (disk.free < min_free_mb * 1_000_000
        or total > max_ledger_mb * 1_000_000):
        raise PaperRefused("disk budget low or SQLite evidence above bounded limit")
    return {"disk_free_mb_at_check": disk.free // 1_000_000,
            "sqlite_bytes_at_check": total}


def _claim_owner(ledger: Path) -> tuple[Path, str]:
    path = ledger.with_name(ledger.name + ".supervisor-owner")
    if path.exists() or path.is_symlink():
        raise PaperRefused("operator review: existing supervisor owner fence; no dual processes")
    token = uuid.uuid4().hex
    with path.open("xb") as file:
        file.write(_canonical_bytes({
            "schema": SCHEMA, "owner_token": token, "pid": os.getpid(),
            "claimed_local_ms": time.time_ns() // 1_000_000,
        }))
        file.flush()
        os.fsync(file.fileno())
    return path, token


def _release_owner(path: Path, token: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise PaperRefused("supervisor owner fence went missing")
    item = json.loads(path.read_text(encoding="utf-8"))
    if item.get("owner_token") != token:
        raise PaperRefused("supervisor owner changed; do not release another owner")
    path.unlink()


def run(
    ledger: Path, evidence_root: Path, *, cycles: int = 1,
    poll_seconds: int = 120, min_free_mb: int = 128,
    max_ledger_mb: int = 256, max_finite_hours: int = 24,
    cycle_run: Callable = cycle, state: Callable = status,
    reporter: Callable = report,
    clock_ms: Callable = lambda: time.time_ns() // 1_000_000,
    monotonic: Callable = time.monotonic,
    sleep: Callable = time.sleep,
) -> dict:
    if (type(cycles) is not int or not 1 <= cycles <= 1440
        or type(poll_seconds) is not int or not 60 <= poll_seconds <= 3600
        or type(max_finite_hours) is not int or not 1 <= max_finite_hours <= 24
        or type(min_free_mb) is not int or not 64 <= min_free_mb <= 32768
        or type(max_ledger_mb) is not int or not 32 <= max_ledger_mb <= 4096
        or (cycles - 1) * poll_seconds > max_finite_hours * 3600):
        raise PaperRefused("finite supervisor budget scope refused")
    owner, token = _claim_owner(ledger)
    evidence = None
    try:
        evidence = Evidence(evidence_root)
        before = state(ledger)
        if (before.get("capital_readiness") != "REAL_CAPITAL_NOT_AUTHORIZED"
            or before.get("real_orders") != 0
            or before.get("interval") not in PERIOD_MS):
            raise PaperRefused("read-only simulation-only account not verified")
        start = monotonic()
        last_wall = clock_ms()
        if type(last_wall) is not int or last_wall <= 0:
            raise PaperRefused("invalid current wall clock")
        ev = lambda kind, ts, attrs: evidence.append(kind, ts, attrs)
        ev("START", last_wall, {
            "cycles_budget": cycles, "poll_seconds": poll_seconds,
            "max_finite_hours": max_finite_hours,
            "source_symbol": before["symbol"],
            "source_timeframe": before["interval"], "real_orders": 0,
        })
        good = 0
        reason = None
        last_verified_performance = None
        for i in range(cycles):
            if i:
                sleep(poll_seconds)
            now = clock_ms()
            if (type(now) is not int or now < last_wall
                or monotonic() < start
                or monotonic() - start > max_finite_hours * 3600):
                reason = "CLOCK_ROLLBACK_OR_BUDGET_EXPIRED"
                break
            last_wall = now
            try:
                bounds = _resource_guard(ledger, min_free_mb, max_ledger_mb)
                response = cycle_run(ledger)
                if (response.get("status") not in _ALLOWED_CYCLE
                    or response.get("real_orders") != 0):
                    raise PaperRefused("unexpected or non-simulation cycle response")
                verified = reporter(ledger)
                if (verified.get("real_orders") != 0
                    or verified.get("real_money_authorized") is not False):
                    raise PaperRefused("research-only quote journal audit failed")
                account = state(ledger)
                if (account["symbol"] != before["symbol"]
                    or account["strategy"] != before["strategy"]
                    or account["interval"] != before["interval"]
                    or account["pending_signals"] != 0
                    or account["simulated_fills"] > account["signal_count"]):
                    raise PaperRefused("paper owner/position state not reconciled")
                # No source continuity claim from merely still-running
                # process: require newest observed CLOSED candle bounded
                # relative to this ACTUAL observed host wallclock.
                last_close = account["last_closed_bar_ms"]
                if (type(last_close) is not int or last_close > now
                    or now - last_close > PERIOD_MS[account["interval"]] + 60_000):
                    raise PaperRefused("source closed candle missing or stale")
                last_verified_performance = verified
                ev("STEP", now, {
                    "cycle": i + 1, "cycle_status": response["status"],
                    "last_closed_bar_ms": last_close,
                    "virtual_fills": account["simulated_fills"],
                    "quote_marks": verified["verified_quote_marks"],
                    "performance_status": verified["performance"],
                    "disk_free_mb_at_check": bounds["disk_free_mb_at_check"],
                    "real_orders": 0,
                })
                good += 1
            except (ValueError, sqlite3.Error, OSError, KeyError,
                    TypeError, AssertionError) as exc:
                reason = type(exc).__name__ + ":" + str(exc)[:130]
                break
        finish = max(last_wall, clock_ms())
        ev("STOP" if reason is None else "HALT", finish, {
            "completed_cycles": good, "reason": reason or "FINITE_BUDGET_REACHED",
            "no_24h_venue_continuity_proof": True, "real_orders": 0,
        })
        evidence.close()
        independent = audit_events(evidence.path)
        if independent["replayed_steps"] != good:
            raise PaperRefused("independent supervisor evidence count disagrees")
        result = {
            "schema": SCHEMA,
            "verdict": PASS if reason is None else FAIL,
            "completed_cycles": good, "requested_cycles": cycles,
            "failure_reason": reason, "evidence_sha256": independent["tail_sha256"],
            "last_verified_performance": last_verified_performance,
            "independent_evidence": independent,
            "local_elapsed_seconds": round(monotonic() - start, 3),
            "uninterrupted_exchange_history_proven": False,
            "continuous_24h_proven": False,
            "prospective_profitable_strategy_proven": False,
            "real_orders": 0, "capital_readiness": "REAL_CAPITAL_NOT_AUTHORIZED",
        }
        with (evidence.root / "summary.json").open("xb") as out:
            out.write(_canonical_bytes(result) + b"\n")
            out.flush()
            os.fsync(out.fileno())
        return result
    finally:
        if evidence is not None:
            evidence.close()
        _release_owner(owner, token)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--evidence-dir", type=Path, required=True)
    p.add_argument("--cycles", type=int, default=1)
    p.add_argument("--poll-seconds", type=int, default=120)
    p.add_argument("--max-finite-hours", type=int, default=24)
    p.add_argument("--simulation-only", action="store_true")
    p.add_argument("--allow-public-market-data", action="store_true")
    p.add_argument("--acknowledge-foreground-only", action="store_true")
    args = p.parse_args(argv)
    if not (args.simulation_only and args.allow_public_market_data
            and args.acknowledge_foreground_only):
        p.error("explicit PUBLIC-DATA + SIMULATION + FOREGROUND only consent required")
    try:
        result = run(args.ledger, args.evidence_dir, cycles=args.cycles,
                     poll_seconds=args.poll_seconds,
                     max_finite_hours=args.max_finite_hours)
    except (ValueError, OSError, TypeError, KeyError,
            sqlite3.Error, AssertionError) as exc:
        print(json.dumps({"verdict": FAIL,
                          "exception_class": type(exc).__name__,
                          "real_orders": 0, "capital_readiness":
                          "REAL_CAPITAL_NOT_AUTHORIZED"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["verdict"] == PASS else 2


if __name__ == "__main__":
    raise SystemExit(main())
