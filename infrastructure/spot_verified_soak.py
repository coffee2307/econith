"""Finite, supervised Spot PUBLIC verified research soak — not a live feed.

Opt-in orchestration over already-reviewed sealed verified microbatches. Each
iteration is a fresh source session, fresh depth snapshot, independent HMAC
receiver ACK chain, and separate durable derived named observer receipts.
The supervisor only accepts a validated *complete* report and atomically
checkpoints its audit hash before beginning the next iteration.

There is NO always-on stream, source continuity across sessions, order routing,
account access, daemon/service, or historical gap repair. A crashed or failed
uncheckpointed iteration is quarantined and requires a new evidence root.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from infrastructure.spot_public_verified_microbatch import (
    CaptureCallable, observe_verified_public_batches,
)

_MANIFEST = "verified_soak_manifest.json"
_REVIEWED_TOPICS = frozenset(("md.ticker", "indicator.volume_delta", "indicator.obi"))
_REVIEWED_SUBSCRIBERS = frozenset(("research_audit", "research_monitor"))


class VerifiedSpotSoakError(RuntimeError):
    """A bounded supervised research soak cannot be certified or resumed."""


def _canonical(data: object) -> bytes:
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _hash(data: object) -> str:
    return hashlib.sha256(_canonical(data)).hexdigest()


def _atomic_checkpoint(destination: Path, report: dict[str, Any]) -> None:
    """Write complete audit checkpoint once, flush, then atomic replace.

    A .pending file left by a crash is NOT success evidence. It can only be
    replaced after strict resume validation of the last committed manifest.
    """
    pending = destination.with_suffix(".pending")
    with open(pending, "xb") as f:
        f.write(_canonical(report) + b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(pending, destination)
    # Best-effort directory metadata sync where supported (POSIX). On Windows
    # atomic replacement and durable file flush still apply.
    try:
        fd = os.open(str(destination.parent), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _read_manifest(root: Path) -> dict[str, Any]:
    try:
        data = json.loads((root / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerifiedSpotSoakError("missing or invalid durable soak checkpoint") from exc
    if type(data) is not dict:
        raise VerifiedSpotSoakError("invalid checkpoint structure")
    return data


def _verify_sealed_round(
    root: Path, index: int, result: dict[str, Any], *,
    fixture: bool,
) -> dict[str, Any]:
    """Cross-check persisted microbatch summary and independently named ACKs."""
    if type(result) is not dict or result.get("acceptance") != (
        "PASSED_BOUNDED_PUBLIC_VERIFIED_MICROBATCH_RESEARCH"
    ):
        raise VerifiedSpotSoakError("missing verified microbatch acceptance")
    sessions = result.get("sessions")
    if (
        type(sessions) is not list or len(sessions) != 1
        or type(sessions[0]) is not dict
        or result.get("completed_cycles") != 1
        or result.get("source_sessions_distinct") is not True
        or result.get("historical_replay_only") is not True
        or result.get("continuous_live_market") is not False
        or result.get("history_continuity_verified") is not False
        or result.get("outage_and_gap_free_stream_proven") is not False
        or result.get("orders_submitted") != 0
        or result.get("execution_started") is not False
        or result.get("legacy_streamer_started") is not False
        or result.get("local_receiver_authenticated") is not True
        or result.get("research_observer_receipts_durable") is not True
        or result.get("named_research_subscriber_receipts_durable") is not True
        or result.get("named_research_subscribers") != 2
        or result.get("other_eventbus_subscribers_durable") is not False
        or result.get("public_tls_peer_confirmed") is (not fixture) is False
    ):
        raise VerifiedSpotSoakError("incomplete or unsafe research batch report")
    s = sessions[0]
    fields = (
        "verified_trades", "originals", "producer_acks",
        "receiver_receipts", "verified_book_update_id",
    )
    if any(type(s.get(field)) is not int or s[field] <= 0 for field in fields):
        raise VerifiedSpotSoakError("missing numeric source/receiver proof")
    sid = s.get("session_id")
    if type(sid) is not str or not sid.startswith("rec_") or len(sid) > 48:
        raise VerifiedSpotSoakError("invalid durable source session identity")
    n = s["verified_trades"]
    wanted = {"md.ticker": n, "indicator.volume_delta": n, "indicator.obi": 1}
    derived = s.get("derived")
    named = s.get("named_research_subscriber_receipts")
    if (
        s.get("cycle") != 1
        or s["producer_acks"] != n or s["receiver_receipts"] != n
        or s["originals"] < n + 1
        or derived != wanted
        or s.get("durable_derived_receipts") != wanted
        or type(named) is not dict
        or set(named) != _REVIEWED_SUBSCRIBERS
        or any(type(v) is not int or v != sum(wanted.values())
               for v in named.values())
        or s.get("research_consumer_receipts_durable") is not True
        or s.get("history_continuity_verified") is not False
        or s.get("historical_replay_only") is not True
        or s.get("legacy_streamer_started") is not False
    ):
        raise VerifiedSpotSoakError("per-source durable derived consumer proof mismatch")
    path = root / f"round_{index:03d}" / "microbatch_progress.json"
    try:
        progress = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerifiedSpotSoakError("microbatch progress evidence unavailable") from exc
    if (
        type(progress) is not dict
        or progress.get("status") != "RESEARCH_ONLY"
        or progress.get("completed_cycles") != 1
        or progress.get("history_continuity_verified") is not False
        or progress.get("orders_submitted") != 0
        or progress.get("sessions") != sessions
    ):
        raise VerifiedSpotSoakError("saved session report differs from verified ACKs")
    raw = root / f"round_{index:03d}" / "original_public.sqlite"
    chain = root / f"round_{index:03d}" / "cycle_01" / "chain_acceptance.json"
    if not raw.is_file() or not chain.is_file():
        raise VerifiedSpotSoakError("original or independent receiver proof missing")
    try:
        chain_data = json.loads(chain.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise VerifiedSpotSoakError("receiver chain proof corrupted") from exc
    if (
        chain_data.get("session_id") != sid
        or chain_data.get("producer_ack_count") != n
        or chain_data.get("independent_receiver_receipt_count") != n
        or chain_data.get("cross_process_authenticated_transport") is not True
        or chain_data.get("orders_submitted") != 0
        or chain_data.get("history_continuity_verified") is not False
    ):
        raise VerifiedSpotSoakError("persisted HMAC receiver ACK proof mismatch")
    return {
        "round": index,
        "session_id": sid,
        "verified_trades": n,
        "originals": s["originals"],
        "source_acks": n,
        "receiver_receipts": n,
        "derived_receipts": sum(wanted.values()),
        "named_receipts": {key: named[key] for key in sorted(named)},
        "session_report_sha256": _hash(progress),
        "receiver_report_sha256": _hash(chain_data),
        "transport_scope": "OFFLINE_FIXTURE" if fixture else "PUBLIC_TLS_WSS_REST",
    }


def _validate_history(root: Path, state: dict[str, Any], *,
                      expected_rounds: int, fixture: bool) -> None:
    if (
        state.get("schema") != 1
        or state.get("requested_rounds") != expected_rounds
        or state.get("fixture") is not fixture
        or state.get("status") not in ("RUNNING", "COMPLETE")
        or state.get("history_continuity_verified") is not False
        or state.get("continuous_live_market") is not False
        or state.get("orders_submitted") != 0
        or type(state.get("rounds")) is not list
    ):
        raise VerifiedSpotSoakError("soak checkpoint identity/phase policy mismatch")
    rows = state["rounds"]
    if not 0 <= len(rows) <= expected_rounds:
        raise VerifiedSpotSoakError("invalid persisted soak progress")
    if state.get("status") == "COMPLETE" and len(rows) != expected_rounds:
        raise VerifiedSpotSoakError("false completed soak manifest")
    if state.get("status") == "RUNNING" and len(rows) == expected_rounds:
        raise VerifiedSpotSoakError("unfinalized complete soak requires review")
    known = set()
    for i, entry in enumerate(rows, start=1):
        if not isinstance(entry, dict) or entry.get("round") != i:
            raise VerifiedSpotSoakError("noncontiguous durable soak progress")
        saved = root / f"round_{i:03d}" / "microbatch_progress.json"
        try:
            progress = json.loads(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VerifiedSpotSoakError("previous sealed batch missing") from exc
        if _hash(progress) != entry.get("session_report_sha256"):
            raise VerifiedSpotSoakError("tampered committed microbatch progress")
        chain_file = root / f"round_{i:03d}" / "cycle_01" / "chain_acceptance.json"
        try:
            chain = json.loads(chain_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VerifiedSpotSoakError("previous receiver ACK proof missing") from exc
        if _hash(chain) != entry.get("receiver_report_sha256"):
            raise VerifiedSpotSoakError("tampered committed receiver ACK proof")
        if entry.get("session_id") in known or (
            progress.get("sessions", [{}])[0].get("session_id") != entry.get("session_id")
        ):
            raise VerifiedSpotSoakError("duplicate or mismatched source session")
        known.add(entry["session_id"])
    # If a process died after opening a new session but BEFORE committing
    # that round's manifest, do not re-use its partial evidence directory.
    actual = {p.name for p in root.glob("round_*") if p.exists()}
    expected = {f"round_{i:03d}" for i in range(1, len(rows) + 1)}
    if actual != expected:
        raise VerifiedSpotSoakError("uncommitted or foreign round found; quarantine evidence")
    if (root / _MANIFEST).with_suffix(".pending").exists():
        raise VerifiedSpotSoakError("uncommitted checkpoint file requires manual review")


async def run_verified_spot_soak(
    *, output_dir: Path, allow_network: bool = False,
    rounds: int = 4, max_frames: int = 160, timeout_s: float = 45,
    max_attempts: int = 2, pause_s: float = 0,
    resume: bool = False,
    capture_factory: Callable[[int, Path], CaptureCallable] | None = None,
    clock_check: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Supervise finite sealed research sessions with auditable checkpoints.

    Normal use requires explicit opt-in, pinned public transport and a fresh
    coarse public time check before each round (provided by approved CLI).
    Injected fixtures are never labeled TLS/peer verified.
    """
    if allow_network is not True:
        raise PermissionError("soak requires explicit public research opt-in")
    if (
        not isinstance(output_dir, Path) or not output_dir.is_absolute()
        or type(rounds) is not int or not 1 <= rounds <= 24
        or type(max_frames) is not int or not 10 <= max_frames <= 2000
        or rounds * max_frames > 48_000
        or type(max_attempts) is not int or not 1 <= max_attempts <= 3
        or type(timeout_s) not in (float, int) or not 5 <= timeout_s <= 120
        or type(pause_s) not in (float, int) or not 0 <= pause_s <= 60
    ):
        raise ValueError("invalid bounded public research soak policy")
    fixture = capture_factory is not None
    if not fixture and not callable(clock_check):
        raise VerifiedSpotSoakError("public clock preflight required for each live round")
    root = output_dir.resolve()
    if output_dir != root:
        raise VerifiedSpotSoakError("research soak root must not be symlinked")
    if not resume:
        if root.exists():
            raise FileExistsError("new empty soak directory required")
        root.mkdir(parents=True, exist_ok=False)
        state: dict[str, Any] = {
            "schema": 1, "requested_rounds": rounds,
            "fixture": fixture, "status": "RUNNING", "rounds": [],
            "continuous_live_market": False,
            "history_continuity_verified": False, "orders_submitted": 0,
        }
        _atomic_checkpoint(root / _MANIFEST, state)
    else:
        state = _read_manifest(root)
        _validate_history(root, state, expected_rounds=rounds, fixture=fixture)
        if state["status"] == "COMPLETE":
            return state
    known_ids = {row["session_id"] for row in state["rounds"]}
    for i in range(len(state["rounds"]) + 1, rounds + 1):
        if clock_check is not None:
            checked = clock_check()
            if asyncio.iscoroutine(checked):
                checked = await checked
            if checked is not True:
                raise VerifiedSpotSoakError("public clock preflight refused research admission")
        path = root / f"round_{i:03d}"
        capture = capture_factory(i, path) if fixture else None
        result = await observe_verified_public_batches(
            output_dir=path, allow_network=True, cycles=1,
            max_frames=max_frames, timeout_s=timeout_s,
            max_attempts=max_attempts, capture_once=capture,
        )
        row = _verify_sealed_round(root, i, result, fixture=fixture)
        if row["session_id"] in known_ids:
            raise VerifiedSpotSoakError("duplicate public Spot session across soak rounds")
        known_ids.add(row["session_id"])
        state["rounds"].append(row)
        state["status"] = "COMPLETE" if i == rounds else "RUNNING"
        _atomic_checkpoint(root / _MANIFEST, state)
        if i < rounds and pause_s:
            await asyncio.sleep(pause_s)
    return state
