"""Operator trial commands remain research-only, source-bounded and honest."""
from __future__ import annotations

import json
import sqlite3

import pytest

from ci import phase1_operator_lab as lab
from infrastructure.spot_forward_supervisor import run_forward_research_supervisor
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import _NOW, source, snapshot


@pytest.mark.asyncio
async def test_operator_report_reopens_real_sqlite_and_never_claims_gap_free(tmp_path):
    trial=(tmp_path/"trial").resolve()
    evidence=trial/"evidence"
    async def capture(directory,sid,index):
        return await observe_forward_spot_research(
            output_dir=directory,session_id=sid,
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,persistent_receiver=True,
        )
    status=await run_forward_research_supervisor(
        output_dir=evidence,allow_public_network=True,
        attempts=2,max_frames=20,timeout_s=20,
        capture_factory=capture,persistent_receiver=True,
    )
    assert status.accepted_sessions==2
    report=lab.inspect_trial(trial)
    assert report["verdict"]=="BOUNDED_SEGMENTS_ACCEPTED_WITH_UNKNOWN_HISTORY_GAPS"
    assert report["passed"]==2
    assert report["unknown_session_boundaries"]==1
    assert report["outage_windows"]==0
    assert report["total_receiver_acks_in_passed_attempts"]==8
    assert report["receiver_process_starts"]==2
    assert report["gap_free_history_verified"] is False
    assert report["continuous_market_feed_certified"] is False
    assert report["default_verified_runtime_cutover_complete"] is False
    assert report["orders_submitted"]==0
    lab._write_report(trial,report)
    assert "Not certified" in (trial/"REPORT.md").read_text(encoding="utf-8")
    assert json.loads((trial/"report.json").read_text())["passed"]==2


@pytest.mark.asyncio
async def test_operator_report_detects_failures_even_after_current_book_restored(tmp_path):
    trial=(tmp_path/"trial_failed").resolve()
    async def capture(directory,sid,index):
        if index==1:
            raise ConnectionResetError("market data lost")
        return await observe_forward_spot_research(
            output_dir=directory,session_id=sid,
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,persistent_receiver=True,
        )
    state=await run_forward_research_supervisor(
        output_dir=trial/"evidence",allow_public_network=True,
        attempts=2,max_frames=20,timeout_s=20,
        capture_factory=capture,persistent_receiver=True,
    )
    assert state.open_outages==0
    report=lab.inspect_trial(trial)
    assert report["failed"]==1 and report["passed"]==1
    assert report["verdict"]=="FAILED_OR_QUARANTINED_SOURCE"
    assert report["unknown_session_boundaries"]==1
    assert report["failure_classes"]=={"ConnectionResetError":1}


def test_operator_trial_requires_explicit_public_data_consent_and_no_files(tmp_path):
    destination=tmp_path/"no_trial"
    with pytest.raises(PermissionError,match="explicit"):
        lab.run_smoke(
            allow_public=False,profile="quick",evidence_base=destination,
        )
    assert not destination.exists()


def test_operator_doctor_warns_on_low_ram_and_has_no_gpu_requirement(
    tmp_path,monkeypatch,
):
    monkeypatch.setattr(lab,"_memory",lambda:(8*2**30,400*2**20))
    status=lab.doctor(evidence_base=tmp_path/"trial")
    assert status["ram_total_gib"]==8
    assert status["ram_available_gib"]<0.5
    assert status["gpu_required"] is False
    assert any("LOW_AVAILABLE_RAM" in x for x in status["warnings"])
    assert "packages" in status
    assert status["recommended_local_profile"]=="quick"


def test_operator_report_refuses_unknown_rewritten_outage_history(tmp_path):
    trial=tmp_path/"report_violation"
    evidence=trial/"evidence"
    evidence.mkdir(parents=True)
    with sqlite3.connect(evidence/"forward_supervisor.sqlite") as db:
        db.execute("""CREATE TABLE forward_watch_plan (
            planned_watch_duration_s REAL,attempts_ceiling INTEGER,
            storage_ceiling_bytes INTEGER,admitted_at_ms INTEGER,
            completed_at_ms INTEGER,completion_code TEXT)""")
        db.execute(
            "INSERT INTO forward_watch_plan VALUES (NULL,1,100000,1780000000000,NULL,NULL)"
        )
        db.execute("""CREATE TABLE forward_watch_attempt (
            iteration INTEGER,session_id TEXT,status TEXT,
            failure_code TEXT,originals INTEGER,receiver_acks INTEGER,
            queue_high_watermark INTEGER,receiver_process_starts INTEGER,
            max_receiver_ack_latency_ms REAL)""")
        db.execute("""CREATE TABLE forward_watch_outage (
            outage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_ms INTEGER,restored_ms INTEGER,last_failure_code TEXT,
            coverage_verified INTEGER)""")
        db.execute("INSERT INTO forward_watch_outage (opened_ms,restored_ms,last_failure_code,coverage_verified) VALUES(1780000000000,NULL,'FAULT',1)")
        db.execute("""CREATE TABLE forward_watch_unobserved_boundary (
            boundary_id INTEGER PRIMARY KEY AUTOINCREMENT,
            previous_iteration INTEGER,next_iteration INTEGER,
            coverage_verified INTEGER)""")
    with pytest.raises(ValueError,match="cannot be relabeled"):
        lab.inspect_trial(trial)


def test_operator_lab_script_explicitly_never_enables_order_routes():
    from pathlib import Path
    base=Path(__file__).resolve().parents[1]
    windows=(base/"scripts/phase1-test.ps1").read_text(encoding="utf-8")
    linux=(base/"scripts/phase1-test.sh").read_text(encoding="utf-8")
    for script in (windows,linux):
        assert "ci.phase1_operator_lab" in script
        assert "--allow-public-market-data" in script
        assert not any(token in script for token in (
            "REALITY","--enable-orders","BINANCE_API_KEY",
            "docker compose up","ECONITH_SPOT_INGRESS_POLICY=",
        ))


@pytest.mark.parametrize("profile,frames,watch_hours",[
    ("diagnostic","500",None),
    ("load","1000",None),
    ("hour","1000","1.0"),
    ("day","2000","24.0"),
])
def test_extended_operator_profiles_require_new_pinned_wss_per_segment(
    tmp_path,profile,frames,watch_hours,
):
    trial=(tmp_path/"new_evidence_root").resolve()
    argv=lab._profile_launch_argv(
        profile=profile,trial=trial,python_executable="python-3.12",
    )
    assert argv[:3]==["python-3.12","-m","ci.phase1_forward_endurance_trial"]
    assert argv.count("--allow-public-market-data")==1
    assert argv.count("--persistent-receiver")==1
    assert argv.count("--single-socket")==1
    assert argv.count("--fresh-socket-per-segment")==1
    assert argv[argv.index("--max-frames")+1]==frames
    assert argv[argv.index("--output-dir")+1]==str(trial/"evidence")
    assert (argv[argv.index("--watch-hours")+1] if "--watch-hours" in argv
            else None)==watch_hours
    if profile in ("hour","day"):
        assert argv.count("--max-consecutive-clock-failures")==1
        assert argv[argv.index("--max-consecutive-clock-failures")+1]=="3"
    else:
        assert "--max-consecutive-clock-failures" not in argv
    assert not any(
        keyword in argv for keyword in
        ("--enable-orders","REALITY","--api-key","--secret")
    )


def test_quick_stays_bounded_and_does_not_claim_fresh_long_watch(tmp_path):
    argv=lab._profile_launch_argv(
        profile="quick",trial=(tmp_path/"quick_trial").resolve(),
        python_executable="python-3.12",
    )
    assert argv[argv.index("--max-frames")+1]=="160"
    assert "--watch-hours" not in argv
    assert "--fresh-socket-per-segment" not in argv
    with pytest.raises(ValueError,match="invalid public trial profile"):
        lab._profile_launch_argv(
            profile="live_trade",trial=(tmp_path/"bad").resolve(),
            python_executable="python-3.12",
        )
    with pytest.raises(ValueError,match="absolute"):
        lab._profile_launch_argv(
            profile="hour",trial=__import__("pathlib").Path("relative"),
            python_executable="python-3.12",
        )


def test_load_operator_profile_is_bounded_and_no_duration_certificate(tmp_path):
    config=lab._PROFILES["load"]
    assert config=={
        "attempts":3,"max_frames":1000,"timeout_s":120,
        "max_storage_mb":200,"watch_hours":None,
    }
    trial=(tmp_path/"load_trial").resolve()
    argv=lab._profile_launch_argv(
        profile="load",trial=trial,python_executable="python-3.12",
    )
    assert "--watch-hours" not in argv
    assert "--max-consecutive-clock-failures" not in argv
    assert "--fresh-socket-per-segment" in argv
    assert argv[argv.index("--attempts")+1]=="3"
    assert argv[argv.index("--max-frames")+1]=="1000"
    assert argv[argv.index("--max-storage-mb")+1]=="200"
    windows=(__import__("pathlib").Path(__file__).resolve().parents[1]/
             "scripts/phase1-test.ps1").read_text(encoding="utf-8")
    linux=(__import__("pathlib").Path(__file__).resolve().parents[1]/
           "scripts/phase1-test.sh").read_text(encoding="utf-8")
    assert '"Load"' in windows and "--profile load" in windows
    assert "diagnostic|load|hour" in linux
    assert "setup|doctor|quick|diagnostic|load|hour" in linux
