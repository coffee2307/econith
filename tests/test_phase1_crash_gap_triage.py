"""P1-14 read-only crash triage, chain integrity and true hard-exit fence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from ci.phase1_crash_gap_triage import inspect, main, CrashTriageRefused, SCHEMA
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_forward_owner import ForwardOwnershipDenied
from tests.test_spot_forward_verified_research import source, snapshot, _NOW


@pytest.fixture
def closed_source(tmp_path):
    root = (tmp_path / "closed").resolve()
    report = asyncio.run(observe_forward_spot_research(
        output_dir=root, session_id="rec_p114_closed",
        stream=source(_NOW()), snapshot_fetch=snapshot,
        max_frames=20, timeout_s=20, persistent_receiver=True,
    ))
    assert report.session_complete
    assert report.receiver_receipts == report.forward_checkpoints == 4
    return root


def test_good_sealed_session_is_locally_consistent_but_not_continuous(closed_source, capsys):
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in closed_source.glob("*.sqlite")
    }
    result = inspect(closed_source)
    assert result["schema"] == SCHEMA
    assert result["verdict"] == "CLOSED_LOCAL_LEDGER_PREFIX_CONSISTENT_ONLY"
    assert result["source_session_closed"] is True
    assert result["owner_fence_present"] is False
    assert result["original_and_atomic_intent_rows"] >= 5
    assert result["outbox_envelopes"] == 4
    assert result["outbox_derived_acked"] == 4
    assert result["independent_receiver_receipts"] == 4
    assert result["durable_checkpoints"] == 4
    assert result["receiver_receipts_ahead_of_outbox_ack"] == 0
    assert result["derived_acked_but_not_checkpointed"] == 0
    assert result["source_session_continuity_certified"] is False
    assert result["unknown_market_history_during_recovery"] is True
    assert result["automatic_lock_reclamation_authorized"] is False
    assert result["mutation_performed"] is False
    assert result["real_orders_submitted"] == 0
    assert main(["--trial-dir", str(closed_source)]) == 0
    assert "CLOSED_LOCAL_LEDGER_PREFIX_CONSISTENT_ONLY" in capsys.readouterr().out
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in closed_source.glob("*.sqlite")
    }
    assert before == after


def test_owner_fence_never_auto_cleared(closed_source, capsys):
    fence = closed_source / ".forward_producer.lock"
    fence.write_text("SIMULATION_CAPTURE\n")
    try:
        report = inspect(closed_source)
        assert report["verdict"] == "QUARANTINED_MANUAL_OWNER_AND_RECOVERY_REVIEW"
        assert report["owner_liveness_verified"] is False
        assert main(["--trial-dir", str(closed_source)]) == 2
        assert fence.is_file()
        assert "QUARANTINED" in capsys.readouterr().out
    finally:
        fence.unlink()


def test_receiver_ack_but_missing_checkpoint_suffix_is_classified_not_repaired(
    closed_source,
):
    db = closed_source / "forward_checkpoints.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM forward_checkpoint WHERE ordinal=4")
    report = inspect(closed_source)
    assert report["verdict"] == "QUARANTINED_MANUAL_OWNER_AND_RECOVERY_REVIEW"
    assert report["durable_checkpoints"] == 3
    assert report["derived_acked_but_not_checkpointed"] == 1
    assert report["independent_receiver_receipts"] == 4
    assert report["mutation_performed"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM forward_checkpoint").fetchone()[0] == 3


def test_modified_middle_checkpoint_hash_refused_not_marked_repairable(closed_source):
    with sqlite3.connect(closed_source / "forward_checkpoints.sqlite") as db:
        db.execute("UPDATE forward_checkpoint SET checkpoint_sha256=? WHERE ordinal=2",
                   ("f" * 64,))
    with pytest.raises(CrashTriageRefused, match="checkpoint"):
        inspect(closed_source)


def test_missing_receiver_owned_ack_refused(closed_source):
    with sqlite3.connect(closed_source / "independent_receiver.sqlite") as db:
        db.execute(
            "DELETE FROM offline_independent_projection_receipts WHERE ingest_id="
            "(SELECT MIN(ingest_id) FROM offline_independent_projection_receipts)"
        )
    with pytest.raises(CrashTriageRefused, match="independently owned ACK"):
        inspect(closed_source)


def test_rehashed_original_intent_rejected_against_immutable_outbox(closed_source):
    with sqlite3.connect(closed_source / "forward_outbox.sqlite") as db:
        cid = db.execute("SELECT MIN(ingest_id) FROM forward_outbox").fetchone()[0]
    with sqlite3.connect(closed_source / "original.sqlite") as db:
        raw = db.execute(
            "SELECT raw_blob FROM offline_spot_capture_records WHERE capture_id=?",
            (cid,),
        ).fetchone()[0]
        assert b"BTCUSDT" in raw
        forged = raw.replace(b"BTCUSDT", b"ETHUSDT")
        digest = hashlib.sha256(forged).hexdigest()
        db.execute(
            "UPDATE offline_spot_capture_records SET raw_blob=?,sha256=? WHERE capture_id=?",
            (forged, digest, cid),
        )
        db.execute(
            "UPDATE offline_forward_capture_intents SET sha256=? WHERE capture_id=?",
            (digest, cid),
        )
    with pytest.raises(CrashTriageRefused, match="original SHA"):
        inspect(closed_source)


def test_bad_source_intent_sha_rejected(closed_source):
    with sqlite3.connect(closed_source / "original.sqlite") as db:
        db.execute(
            "UPDATE offline_forward_capture_intents SET sha256=? WHERE capture_id=1",
            ("f" * 64,),
        )
    with pytest.raises(CrashTriageRefused, match="source/intent"):
        inspect(closed_source)


def test_missing_source_evidence_or_aliased_owner_lock_refused(closed_source):
    (closed_source / ".forward_producer.lock").symlink_to(
        closed_source / "original.sqlite"
    )
    with pytest.raises(CrashTriageRefused, match="symlink"):
        inspect(closed_source)


def test_true_hard_kill_leaves_open_source_and_fence_then_denies_recovery(
    tmp_path, capsys,
):
    root = (tmp_path / "hardexit").resolve()
    script = """
import asyncio, os, sys
from pathlib import Path
from infrastructure.spot_forward_checkpoints import OfflineForwardSessionCheckpoints
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import source,snapshot,_NOW

original=OfflineForwardSessionCheckpoints.append_live

def kill_after_verified_commit(self,*args,**kwargs):
    result=original(self,*args,**kwargs)
    os._exit(47)  # checkpoint FULL COMMIT happened; owner must stay orphaned.

OfflineForwardSessionCheckpoints.append_live=kill_after_verified_commit
asyncio.run(observe_forward_spot_research(
    output_dir=Path(sys.argv[1]),session_id='rec_p114_hardexit',
    stream=source(_NOW()),snapshot_fetch=snapshot,max_frames=20,
    timeout_s=20,persistent_receiver=True,
))
"""
    child = subprocess.run(
        [sys.executable, "-c", script, str(root)],
        capture_output=True, text=True, timeout=60,
    )
    assert child.returncode == 47, child.stderr
    assert (root / ".forward_producer.lock").exists()
    before = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in ("original.sqlite", "forward_outbox.sqlite",
                     "forward_checkpoints.sqlite")
    }
    result = inspect(root)
    assert result["verdict"] == "QUARANTINED_MANUAL_OWNER_AND_RECOVERY_REVIEW"
    assert result["owner_fence_present"] is True
    assert result["source_session_closed"] is False
    assert result["durable_checkpoints"] >= 1
    assert result["independent_receiver_receipts"] >= 1
    assert result["source_session_continuity_certified"] is False
    assert main(["--trial-dir", str(root)]) == 2
    assert "QUARANTINED" in capsys.readouterr().out
    with pytest.raises(ForwardOwnershipDenied):
        asyncio.run(recover_staged_forward_spot(
            root=root, session_id="rec_p114_hardexit",
        ))
    assert (root / ".forward_producer.lock").exists()
    after = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in before
    }
    assert before == after


def test_cli_refuses_missing_evidence_without_creating_files(tmp_path, capsys):
    root = (tmp_path / "empty").resolve()
    root.mkdir()
    assert main(["--trial-dir", str(root)]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "FORENSIC_REFUSED_UNTRUSTED_LOCAL_EVIDENCE"
    assert payload["mutation_performed"] is False
    assert payload["real_orders_submitted"] == 0
    assert list(root.iterdir()) == []
