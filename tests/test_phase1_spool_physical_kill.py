"""Real subprocess hard exit after signed ACK/checkpoint, before spool cursor.

Offline fixture ONLY. The orphan owner fence must survive hard exit.
No automatic WSS reattachment and no failed-source certification.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

from core.mode import QuantMode
from ci.phase1_spool_forensic_inspect import inspect
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_projection_durable_cursor import OfflineSpotProjectionCursor
from infrastructure.spot_forward_owner import (
    ForwardOwnershipDenied, exclusive_forward_owner,
)
import pytest


def _count(path,sql):
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        return db.execute(sql).fetchone()[0]


def test_os_hard_exit_after_checkpoint_blocks_unreviewed_restart(tmp_path):
    root=(tmp_path/"physical_hard_exit").resolve()
    program=r'''
import asyncio,os,sqlite3,sys
from pathlib import Path
from ci.phase1_offline_burst_probe import _source,_snapshot
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from infrastructure.spot_projection_durable_cursor import OfflineSpotProjectionCursor
root=Path(sys.argv[1])
original=OfflineSpotProjectionCursor.advance
def hard_exit_after_checkpoint(self,record):
    f=root/"forward_checkpoints.sqlite"
    if f.is_file():
        with sqlite3.connect(f) as db:
            count=db.execute("SELECT COUNT(*) FROM forward_checkpoint").fetchone()[0]
        if count>=2:
            # The independent durable checkpoint is already COMMITTED.
            # Deliberately bypass all Python finally/contextmanager cleanup.
            os._exit(47)
    return original(self,record)
OfflineSpotProjectionCursor.advance=hard_exit_after_checkpoint
asyncio.run(observe_forward_spot_research(
    output_dir=root,session_id="rec_real_hard_exit",
    stream=_source(60,60),snapshot_fetch=_snapshot,
    max_frames=60,timeout_s=30,queue_capacity=128,
    persistent_receiver=True,spooled_in_session=True,
    experiment_reuse_derived_connections=True,
    max_projector_lag_ms=5000,
))
'''
    result=subprocess.run(
        [sys.executable,"-c",program,str(root)],
        capture_output=True,text=True,timeout=60,
    )
    assert result.returncode==47, result.stderr[-1200:]
    assert (root/".forward_producer.lock").is_file()
    with pytest.raises(ForwardOwnershipDenied,match="orphaned"):
        with exclusive_forward_owner(root,role="STAGED_RECOVERY"):
            pytest.fail("unreviewed recovery must not acquire orphan source")

    src=_count(root/"original.sqlite",
               "SELECT COUNT(*) FROM offline_spot_capture_records")
    cursor=_count(root/"projection_cursor.sqlite",
                  "SELECT last_capture_id FROM spot_projection_cursor")
    checkpoint=_count(root/"forward_checkpoints.sqlite",
                      "SELECT COUNT(*) FROM forward_checkpoint")
    receiver=_count(root/"independent_receiver.sqlite",
                    "SELECT COUNT(*) FROM offline_independent_projection_receipts")
    assert src>=3 and checkpoint>=2 and receiver>=checkpoint
    assert cursor<src
    assert not (root/"forward_complete.json").exists()
    assert _count(root/"original.sqlite",
                  "SELECT COUNT(*) FROM offline_spot_capture_sessions WHERE closed_at_ms IS NULL")==1
    journal=OfflineSpotCaptureJournal(
        root/"original.sqlite",mode_provider=lambda: QuantMode.SIMULATION,
        forward_capture_audit=True,
    )
    reopened=OfflineSpotProjectionCursor(
        root/"projection_cursor.sqlite",journal=journal,
        session_id="rec_real_hard_exit",
    )
    assert reopened.read_verified().last_capture_id==cursor
    audit=inspect(root)
    assert audit["inspection_status"]=="ORPHAN_FENCE_REQUIRES_OPERATOR_REVIEW"
    assert audit["originals_not_projected"]==src-cursor
    assert audit["orphan_owner_fence_present"]
    assert audit["source_continuity_certified"] is False
    assert audit["receiver_hmac_cryptography_certified_by_this_inspector"] is False
    assert audit["mutations_performed"] is False
    # Evidence and orphan marker are intentionally NOT cleaned in this test.
