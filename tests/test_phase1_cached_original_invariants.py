"""Opt-in original spool cache must preserve raw+intent atomicity and WAL FULL."""
from __future__ import annotations

import sqlite3
import subprocess
import sys

import pytest

from ci.phase1_offline_burst_probe import _source, _snapshot
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_forward_verified_research import observe_forward_spot_research


def test_source_cached_handle_full_wal_and_distinct_independent_reader(tmp_path):
    path=(tmp_path/"original.sqlite").resolve()
    journal=OfflineSpotCaptureJournal(
        path,mode_provider=lambda:QuantMode.SIMULATION,
        forward_capture_audit=True,reuse_connection=True,
    )
    try:
        with journal._connect() as a:
            assert a.execute("PRAGMA journal_mode").fetchone()[0].lower()=="wal"
            assert a.execute("PRAGMA synchronous").fetchone()[0]==2
            assert a.execute("PRAGMA foreign_keys").fetchone()[0]==1
        with journal._connect() as b:
            assert b is a
        with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as independent:
            assert independent.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    finally:
        journal.close()


@pytest.mark.asyncio
async def test_original_cached_pipeline_matches_independent_intents(tmp_path):
    root=(tmp_path/"source_cache_30").resolve()
    result=await observe_forward_spot_research(
        output_dir=root,session_id="rec_orig_cached30",
        stream=_source(30,60),snapshot_fetch=_snapshot,
        max_frames=30,timeout_s=30,persistent_receiver=True,
        spooled_in_session=True,experiment_reuse_derived_connections=True,
        experiment_reuse_producer_connections=True,
        experiment_reuse_receiver_connection=True,
        experiment_reuse_original_connection=True,
        diagnose_pressure=True,
    )
    assert result.original_records==31
    assert result.spool_original_cursor==31
    assert result.forward_checkpoints==result.receiver_receipts
    assert result.session_complete and result.orders_submitted==0
    with sqlite3.connect((root/"original.sqlite").as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0]==31
        assert db.execute("SELECT COUNT(*) FROM offline_forward_capture_intents").fetchone()[0]==31
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records AS r LEFT JOIN offline_forward_capture_intents AS i ON i.capture_id=r.capture_id WHERE i.capture_id IS NULL OR i.sha256<>r.sha256").fetchone()[0]==0


@pytest.mark.asyncio
async def test_source_cache_denied_for_untested_public_or_partial_mode(tmp_path):
    root=(tmp_path/"denied").resolve()
    with pytest.raises(PermissionError,match="explicit fully reviewed bounded spool"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_orig_denied",
            stream=_source(30,60),snapshot_fetch=_snapshot,
            max_frames=30,timeout_s=30,persistent_receiver=True,
            spooled_in_session=True,experiment_reuse_original_connection=True,
        )
    assert not root.exists()


def test_physical_hard_exit_after_raw_intent_full_commit(tmp_path):
    path=(tmp_path/"original_hard_exit.sqlite").resolve()
    program=r'''
import os,sys,time,json
from pathlib import Path
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
p=Path(sys.argv[1])
j=OfflineSpotCaptureJournal(p,mode_provider=lambda: QuantMode.SIMULATION,
                            forward_capture_audit=True,reuse_connection=True)
now=time.time_ns()//1_000_000
j.start_session("rec_orig_kill",symbol="BTCUSDT",source="offline_fixture",
                ws_endpoint="wss://data-stream.binance.vision/stream",opened_at_ms=now)
trade={"e":"aggTrade","s":"BTCUSDT","E":now,"T":now-10,"a":500,"f":500,
       "l":500,"p":"100","q":"0.1","m":False}
frame=json.dumps({"stream":"btcusdt@aggTrade","data":trade}).encode()
j.record_ws("rec_orig_kill",frame,stream_kind="aggTrade",received_at_ms=now)
os._exit(67)
'''
    r=subprocess.run([sys.executable,"-c",program,str(path)],
                     capture_output=True,text=True,timeout=15)
    assert r.returncode==67,r.stderr[-1200:]
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM offline_forward_capture_intents").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records r JOIN offline_forward_capture_intents i ON i.capture_id=r.capture_id AND i.sha256=r.sha256").fetchone()[0]==1
    reopened=OfflineSpotCaptureJournal(
        path,mode_provider=lambda:QuantMode.SIMULATION,forward_capture_audit=True,
    )
    proof=reopened.verify_forward_capture_inventory("rec_orig_kill")
    assert len(proof)==1


def test_intent_trigger_failure_rolls_back_raw_and_capture_intent(tmp_path):
    path=(tmp_path/"original_rollback.sqlite").resolve()
    j=OfflineSpotCaptureJournal(path,mode_provider=lambda: QuantMode.SIMULATION,
                                forward_capture_audit=True,reuse_connection=True)
    import json,time
    now=time.time_ns()//1_000_000
    j.start_session("rec_orig_rollback",symbol="BTCUSDT",source="offline_fixture",
                    ws_endpoint="wss://data-stream.binance.vision/stream",opened_at_ms=now)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER deny_intent BEFORE INSERT ON offline_forward_capture_intents BEGIN SELECT RAISE(ABORT,'forced intent failure'); END")
    trade={"e":"aggTrade","s":"BTCUSDT","E":now,"T":now-10,"a":500,"f":500,"l":500,"p":"100","q":"0.1","m":False}
    frame=json.dumps({"stream":"btcusdt@aggTrade","data":trade}).encode()
    with pytest.raises(sqlite3.DatabaseError,match="forced intent"):
        j.record_ws("rec_orig_rollback",frame,stream_kind="aggTrade",received_at_ms=now)
    with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0]==0
        assert db.execute("SELECT COUNT(*) FROM offline_forward_capture_intents").fetchone()[0]==0
    j.close()
