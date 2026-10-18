"""Bounded 3x public trial report must not hide one failed session."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ci import phase1_public_live_spool_matrix as operator


@pytest.mark.asyncio
async def test_three_successes_are_only_finite_research(tmp_path,monkeypatch):
    calls=[]
    async def fake_run(root:Path,*,frames:int,timeout_s:float,max_lag_ms:int):
        calls.append(root)
        root.mkdir()
        return {
            "original_records":frames+1,
            "receiver_receipts":frames-3,
            "forward_checkpoints":frames-3,
            "spool_original_cursor":frames+1,
            "queue_high_watermark":20,
            "max_projector_lag_ms":350,
            "max_spool_pending_originals":15,
            "publication_in_session":True,
        }
    monkeypatch.setattr(operator,"run",fake_run)
    root=(tmp_path/"matrix").resolve()
    root.mkdir()
    result=await operator.matrix(root,frames=1000,max_lag_ms=5000)
    assert len(calls)==3
    assert all(x.parent==root for x in calls)
    assert result["verdict"]=="PASSED_FINITE_PUBLIC_SPOOL_3X1000_RESEARCH_ONLY"
    assert result["passed"]==3
    assert result["continuous_market_feed_certified"] is False
    assert result["institutional_phase1_complete"] is False
    assert result["orders_submitted"]==0
    assert json.loads((root/"matrix_report.json").read_text())["passed"]==3


@pytest.mark.asyncio
async def test_matrix_preserves_failed_middle_segment(tmp_path,monkeypatch):
    sequence=[0]
    async def fake_run(root,*,frames,timeout_s,max_lag_ms):
        sequence[0]+=1
        if sequence[0]==2:
            raise RuntimeError("simulated rejected source")
        root.mkdir()
        return {
            "original_records":frames+1,"receiver_receipts":frames-1,
            "forward_checkpoints":frames-1,"spool_original_cursor":frames+1,
            "queue_high_watermark":12,"max_projector_lag_ms":100,
            "max_spool_pending_originals":3,"publication_in_session":True,
        }
    monkeypatch.setattr(operator,"run",fake_run)
    root=(tmp_path/"matrix_failed").resolve()
    root.mkdir()
    report=await operator.matrix(root,frames=1000,max_lag_ms=5000)
    assert report["passed"]==2 and report["failed"]==1
    assert report["segments"][1]["status"]=="FAILED_OR_QUARANTINED_SOURCE"
    assert report["verdict"]=="FAILED_OR_BOUNDED_RESEARCH_ONLY"
    assert report["history_gap_free_verified"] is False
