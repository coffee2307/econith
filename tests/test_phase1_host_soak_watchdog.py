"""P1-15 local host watchdog, monotonic source/HMAC counters and crash refusal."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from ci.phase1_host_soak_watchdog import (
    FINITE_PASS, HostWatchRefused, MAX_AGE_MS, observe, sample, verify_trace,
)
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import source, snapshot, _NOW


def host_metrics(root):
    return {
        "available_memory_bytes": 4_000_000_000,
        "disk_free_bytes": 20_000_000_000,
        "evidence_file_bytes": 450_000,
    }


@pytest.fixture
def closed(tmp_path):
    root = (tmp_path / "source").resolve()
    report = asyncio.run(observe_forward_spot_research(
        output_dir=root, session_id="rec_host_soak_fixture",
        stream=source(_NOW()), snapshot_fetch=snapshot,
        max_frames=20, timeout_s=20, persistent_receiver=True,
        spooled_in_session=True,
    ))
    assert report.receiver_receipts == report.forward_checkpoints == 4
    return root


def test_closed_source_snapshot_counts_match_but_never_24h(closed):
    snap = sample(closed, metrics=host_metrics)
    assert snap["state"] == "CLOSED_LOCALLY_COHERENT_NOT_24H"
    assert snap["source_originals"] == snap["source_intents"]
    assert snap["source_projection_cursor"] == snap["source_originals"]
    assert snap["staged_projections"] == snap["derived_acked_projections"] == 4
    assert snap["receiver_receipts"] == snap["durable_checkpoints"] == 4
    assert snap["source_owner_marker_present"] is False
    assert snap["market_history_complete"] is False
    assert snap["orders_submitted"] == 0


@pytest.mark.parametrize("key,value", [
    ("available_memory_bytes", 1_499_999_999),
    ("disk_free_bytes", 1_999_999_999),
    ("evidence_file_bytes", 500_000_001),
    ("available_memory_bytes", None),
])
def test_resource_budget_blocks_research_observer(closed, key, value):
    def limited(root):
        state = host_metrics(root)
        state[key] = value
        return state
    with pytest.raises(HostWatchRefused, match="resource|budget"):
        sample(closed, metrics=limited)


def test_forged_atomic_intent_count_refused(closed):
    with sqlite3.connect(closed / "original.sqlite") as db:
        db.execute("DELETE FROM offline_forward_capture_intents WHERE capture_id=1")
    with pytest.raises(HostWatchRefused, match="inventory"):
        sample(closed, metrics=host_metrics)


def test_checkpoint_ahead_of_receiver_refused(closed):
    with sqlite3.connect(closed / "independent_receiver.sqlite") as db:
        db.execute(
            "DELETE FROM offline_independent_projection_receipts WHERE ingest_id="
            "(SELECT MIN(ingest_id) FROM offline_independent_projection_receipts)"
        )
    with pytest.raises(HostWatchRefused, match="cursor|suffix"):
        sample(closed, metrics=host_metrics)


def test_closed_missing_checkpoint_suffix_is_not_pass(closed):
    with sqlite3.connect(closed / "forward_checkpoints.sqlite") as db:
        db.execute("DELETE FROM forward_checkpoint WHERE ordinal=4")
    with pytest.raises(HostWatchRefused, match="suffix"):
        sample(closed, metrics=host_metrics)


def test_orphaned_open_source_without_owner_never_faked_as_closed(closed):
    with sqlite3.connect(closed / "original.sqlite") as db:
        db.execute("UPDATE offline_spot_capture_sessions SET closed_at_ms=NULL")
    with pytest.raises(HostWatchRefused, match="owner"):
        sample(closed, metrics=host_metrics)


def test_active_source_stale_timestamp_quarantined(closed):
    with sqlite3.connect(closed / "original.sqlite") as db:
        db.execute("UPDATE offline_spot_capture_sessions SET closed_at_ms=NULL")
    (closed / ".forward_producer.lock").write_text("SIMULATION_CAPTURE\n")
    snap = sample(closed, metrics=host_metrics,
                  now_ms=int(__import__("time").time() * 1000))
    assert snap["state"] == "SOURCE_OPEN"
    with pytest.raises(HostWatchRefused, match="stale"):
        sample(closed, metrics=host_metrics,
               now_ms=closed_last_ms(closed) + MAX_AGE_MS + 2000)


def closed_last_ms(root):
    with sqlite3.connect(root / "original.sqlite") as db:
        return db.execute(
            "SELECT MAX(received_at_ms) FROM offline_spot_capture_records"
        ).fetchone()[0]


def _state(index, kind, *, n=0, session="a" * 64):
    return {
        "state": kind,
        "source_identity_sha256": session,
        "local_observed_ms": 1_800_000_000_000 + index * 1000,
        "source_originals": n,
        "source_intents": n,
        "receiver_receipts": n,
        "durable_checkpoints": n,
        "source_projection_cursor": n,
        "orders_submitted": 0,
    }


@pytest.mark.asyncio
async def test_watchdog_fsync_hash_chain_accepts_finite_source_but_not_24h(tmp_path):
    source_root = (tmp_path / "fresh-source").resolve()
    watch_root = (tmp_path / "fresh-watch").resolve()
    points = [
        {"state": "WAITING_FOR_SOURCE"},
        _state(1, "SOURCE_OPEN", n=1),
        _state(2, "SOURCE_OPEN", n=3),
        _state(3, "CLOSED_LOCALLY_COHERENT_NOT_24H", n=3),
    ]
    def scripted(root, **kw):
        return points.pop(0)
    result = await observe(
        source_root, watch_root, max_watch_s=10,
        poll_s=.2, snapshot_fn=scripted, metrics=host_metrics,
    )
    assert result["verdict"] == FINITE_PASS
    assert result["samples"] == 4
    assert result["source_open_observations"] == 2
    assert result["continuous_24h_qualified"] is False
    assert result["auto_restart_authorized"] is False
    report = json.loads((watch_root / "watch_result.json").read_text())
    assert report["verdict"] == FINITE_PASS
    independent = verify_trace(watch_root / "observations.jsonl")
    assert independent["trace_observations"] == 4
    assert independent["continuous_market_history_proven"] is False


@pytest.mark.asyncio
async def test_missing_source_never_successfully_expires_without_proof(tmp_path):
    source_root = (tmp_path / "missing").resolve()
    watch_root = (tmp_path / "monitor").resolve()
    with pytest.raises(HostWatchRefused):
        await observe(
            source_root, watch_root, max_watch_s=10,
            poll_s=.2, startup_grace_s=5,
            snapshot_fn=lambda *a, **k: {"state": "WAITING_FOR_SOURCE"},
            metrics=host_metrics,
        )
    saved = json.loads((watch_root / "watch_result.json").read_text())
    assert saved["verdict"] != FINITE_PASS


@pytest.mark.asyncio
async def test_clock_or_progress_rollback_invalidates_hash_chained_trace(tmp_path):
    source_root = (tmp_path / "input").resolve()
    watch_root = (tmp_path / "watch").resolve()
    points = [
        _state(0, "SOURCE_OPEN", n=4),
        _state(1, "SOURCE_OPEN", n=2),
        _state(2, "CLOSED_LOCALLY_COHERENT_NOT_24H", n=4),
    ]
    # Exercise independent trace verifier against a forged yet rehashed
    # sequence; source counters must not move backwards in local time.
    watch_root.mkdir()
    chain = "0" * 64
    trace = watch_root / "observations.jsonl"
    with trace.open("wb") as f:
        for i, point in enumerate(points, 1):
            body = {"ordinal": i, "previous_sha256": chain, "sample": point}
            digest = hashlib.sha256(json.dumps(
                body, sort_keys=True, separators=(",", ":")
            ).encode()).hexdigest()
            f.write(json.dumps(dict(body, sha256=digest),
                               sort_keys=True, separators=(",", ":")).encode() + b"\n")
            chain = digest
    with pytest.raises(HostWatchRefused, match="rolled back"):
        verify_trace(trace)


def test_truncated_or_modified_watchdog_trace_fails_closed(tmp_path):
    trace = (tmp_path / "observations.jsonl").resolve()
    trace.write_text('{"bad":"fake"}\n')
    with pytest.raises(HostWatchRefused):
        verify_trace(trace)


@pytest.mark.asyncio
async def test_independent_trace_failure_cannot_leave_persisted_false_pass(
    tmp_path, monkeypatch,
):
    import ci.phase1_host_soak_watchdog as watchdog
    root = (tmp_path / "input").resolve()
    watch = (tmp_path / "watch").resolve()
    points = [
        _state(0, "SOURCE_OPEN", n=3),
        _state(1, "CLOSED_LOCALLY_COHERENT_NOT_24H", n=3),
    ]
    def refuse_trace(path):
        raise HostWatchRefused("injected re-audit mismatch")
    monkeypatch.setattr(watchdog, "verify_trace", refuse_trace)
    with pytest.raises(HostWatchRefused, match="re-audit"):
        await watchdog.observe(
            root, watch, max_watch_s=10, poll_s=.2,
            snapshot_fn=lambda *a, **k: points.pop(0),
            metrics=host_metrics,
        )
    report = json.loads((watch / "watch_result.json").read_text())
    assert report["verdict"] == "PENDING_INDEPENDENT_TRACE_REAUDIT"
    assert report["continuous_24h_qualified"] is False
    assert not (watch / "watch_result.pending").exists()


@pytest.mark.asyncio
async def test_regressing_receiver_counter_quarantines_during_live_observation(
    tmp_path,
):
    root = (tmp_path / "source").resolve()
    watch = (tmp_path / "watch").resolve()
    points = [
        _state(0, "SOURCE_OPEN", n=5),
        _state(1, "SOURCE_OPEN", n=3),
    ]
    with pytest.raises(HostWatchRefused, match="regressed"):
        await observe(
            root, watch, max_watch_s=10, poll_s=.2,
            snapshot_fn=lambda *a, **k: points.pop(0),
            metrics=host_metrics,
        )
    report = json.loads((watch / "watch_result.json").read_text())
    assert report["verdict"] != FINITE_PASS
    assert report["source_was_active"] is True
