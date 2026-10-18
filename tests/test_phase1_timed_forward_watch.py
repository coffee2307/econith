"""Durable operator-duration forward watch; don't claim continuous history."""
from __future__ import annotations

import sqlite3

import pytest

from infrastructure.spot_forward_supervisor import (
    ForwardSupervisorError,run_forward_research_supervisor,
)
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import _NOW, source, snapshot


async def real_fixture(directory,sid,index):
    return await observe_forward_spot_research(
        output_dir=directory,session_id=sid,stream=source(_NOW()),
        snapshot_fetch=snapshot,max_frames=20,timeout_s=20,
        persistent_receiver=True,
    )


@pytest.mark.asyncio
async def test_operator_time_target_uses_real_committed_sources_and_retains_gaps(tmp_path):
    root=(tmp_path/"duration_watch").resolve()
    monotonic=[0.]
    def clock(): return monotonic[0]
    async def capture(directory,sid,index):
        result=await real_fixture(directory,sid,index)
        monotonic[0]+=3
        return result
    result=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,
        attempts=6,max_frames=20,timeout_s=20,
        persistent_receiver=True,watch_duration_s=5,
        capture_factory=capture,_monotonic_s=clock,
    )
    assert result.state=="WATCH_TARGET_REACHED_WITH_UNKNOWN_GAPS"
    assert result.planned_watch_duration_s==5
    assert result.watch_target_reached is True
    assert result.observed_watch_duration_s==6
    assert result.attempts==result.accepted_sessions==2
    assert result.receiver_process_starts==2
    assert result.total_receiver_receipts==8
    assert result.unobserved_session_boundaries==1
    assert result.history_continuity_verified is False
    assert result.continuous_live_market is False
    assert not (root/".forward_producer.lock").exists()
    with sqlite3.connect(root/"forward_supervisor.sqlite") as db:
        plan=db.execute("""
            SELECT planned_watch_duration_s,attempts_ceiling,
                   storage_ceiling_bytes,completion_code,completed_at_ms
            FROM forward_watch_plan
        """).fetchone()
        boundaries=db.execute("""
            SELECT previous_iteration,next_iteration,coverage_verified,
                   previous_finished_ms,next_started_ms
            FROM forward_watch_unobserved_boundary
        """).fetchall()
    assert plan[0]==5 and plan[1]==6 and plan[2]==100_000_000
    assert plan[3]=="TARGET_REACHED" and plan[4]>0
    assert len(boundaries)==1
    assert boundaries[0][:3]==(1,2,0)
    assert boundaries[0][4]>=boundaries[0][3]


@pytest.mark.asyncio
async def test_attempt_ceiling_never_masquerades_as_elapsed_operator_soak(tmp_path):
    root=(tmp_path/"exhausted").resolve()
    monotonic=[0.]
    async def capture(directory,sid,index):
        result=await real_fixture(directory,sid,index)
        monotonic[0]+=1
        return result
    result=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,attempts=2,
        max_frames=20,timeout_s=20,persistent_receiver=True,
        watch_duration_s=100,capture_factory=capture,
        _monotonic_s=lambda:monotonic[0],
    )
    assert result.state=="WATCH_ATTEMPT_CEILING_REACHED"
    assert result.watch_target_reached is False
    assert result.observed_watch_duration_s==2
    assert result.planned_watch_duration_s==100
    assert result.accepted_sessions==2
    assert result.unobserved_session_boundaries==1
    with sqlite3.connect(root/"forward_supervisor.sqlite") as db:
        code=db.execute(
            "SELECT completion_code FROM forward_watch_plan"
        ).fetchone()[0]
    assert code=="ATTEMPT_CEILING"


@pytest.mark.asyncio
async def test_operator_duration_cannot_disable_persistent_receiver_or_fake_clock(tmp_path):
    root=(tmp_path/"invalid").resolve()
    for kwargs in (
        {"watch_duration_s":5,"persistent_receiver":False},
        {"watch_duration_s":4,"persistent_receiver":True},
        {"watch_duration_s":259201,"persistent_receiver":True},
        {"watch_duration_s":5,"persistent_receiver":True,"attempts":4097},
        {"watch_duration_s":5,"persistent_receiver":True,
         "_monotonic_s":lambda:999},
    ):
        with pytest.raises(ValueError,match="configuration"):
            await run_forward_research_supervisor(
                output_dir=root,allow_public_network=True,
                capture_factory=real_fixture if "_monotonic_s" not in kwargs else None,
                max_frames=20,timeout_s=20,
                **kwargs,
            )
    assert not root.exists()


@pytest.mark.asyncio
async def test_time_window_still_runs_clock_preflight_before_each_source(tmp_path):
    root=(tmp_path/"clock_watch").resolve()
    monotonic=[0.]
    checks=[]
    opens=[]
    async def preflight():
        checks.append(len(checks)+1)
        return True
    async def capture(directory,sid,index):
        opens.append(index)
        result=await real_fixture(directory,sid,index)
        monotonic[0]+=3
        return result
    report=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,
        attempts=4,max_frames=20,timeout_s=20,
        watch_duration_s=5,persistent_receiver=True,
        capture_factory=capture,clock_check=preflight,
        _monotonic_s=lambda:monotonic[0],
    )
    assert report.watch_target_reached
    assert checks==[1,2]
    assert opens==[1,2]
    assert report.unobserved_session_boundaries==1


@pytest.mark.asyncio
async def test_failed_connection_then_recovery_preserves_unknown_gap_between_rounds(tmp_path):
    root=(tmp_path/"failed_watch").resolve()
    monotonic=[0.]
    async def capture(directory,sid,index):
        monotonic[0]+=3
        if index==1:
            raise ConnectionResetError("source lost")
        return await real_fixture(directory,sid,index)
    report=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,
        attempts=3,max_frames=20,timeout_s=20,
        watch_duration_s=5,persistent_receiver=True,
        capture_factory=capture,_monotonic_s=lambda:monotonic[0],
    )
    assert report.watch_target_reached
    assert report.failed_sessions==1
    assert report.accepted_sessions==1
    assert report.outage_windows==1 and report.open_outages==0
    assert report.unobserved_session_boundaries==1
    with sqlite3.connect(root/"forward_supervisor.sqlite") as db:
        boundaries=db.execute(
            "SELECT coverage_verified FROM forward_watch_unobserved_boundary"
        ).fetchall()
        outage=db.execute(
            "SELECT coverage_verified FROM forward_watch_outage"
        ).fetchall()
    assert boundaries==[(0,)] and outage==[(0,)]
