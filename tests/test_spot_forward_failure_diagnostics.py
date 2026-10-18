"""Safe, actionable reason codes for real Binance public-market operator failures."""
from __future__ import annotations

import sqlite3

import pytest

from infrastructure.spot_forward_failure_diagnostics import (
    safe_failure_code,failure_queue_peak,
)
from infrastructure.spot_public_capture_adapter import SpotTransportError
from infrastructure.spot_ingress_capture import SpotCaptureError
from infrastructure.spot_forward_supervisor import run_forward_research_supervisor


def test_transport_safe_reason_is_stable_and_never_leaks_raw_payload():
    exc=SpotTransportError(
        "private hostname or market bytes should never persist",
        reason="QUEUE_BACKPRESSURE",
    )
    exc.ingress_queue_high_watermark=128
    assert safe_failure_code(exc)=="SPOT_TRANSPORT_QUEUE_BACKPRESSURE"
    assert failure_queue_peak(exc)==128
    unknown=SpotTransportError("raw secret",reason="CUSTOM_PLACEHOLDER")
    assert safe_failure_code(unknown)=="SPOT_TRANSPORT_UNCLASSIFIED"
    assert "secret" not in safe_failure_code(unknown)


def test_capture_reason_is_classified_without_storing_live_exchange_message():
    expected={
        "source event is stale versus receipt":"SOURCE_EVENT_STALE",
        "source event is future-dated versus receipt":"SOURCE_EVENT_FUTURE",
        "capture capacity reached; no silent drops":"CAPTURE_STORAGE_CAPACITY",
        "trade event/trade clock lag exceeds tolerance":"SOURCE_TRADE_CLOCK_LAG",
    }
    for message,reason in expected.items():
        exc=SpotCaptureError(message)
        assert safe_failure_code(exc)=="SPOT_CAPTURE_"+reason
    assert safe_failure_code(
        SpotCaptureError("exchange raw bytes: secret never store")
    )=="SPOT_CAPTURE_UNCLASSIFIED"
    assert failure_queue_peak(SpotCaptureError("not instrumented"))==0


def test_nested_transport_reason_can_be_recovered_without_wrapped_message():
    try:
        raise SpotTransportError("sensitive transport url",reason="CAPTURE_TIMEOUT")
    except SpotTransportError as source:
        try:
            raise RuntimeError("sensitive outer bytes") from source
        except RuntimeError as wrapper:
            assert safe_failure_code(wrapper)=="SPOT_TRANSPORT_CAPTURE_TIMEOUT"


@pytest.mark.asyncio
async def test_failed_operator_session_persists_queue_peak_and_reason_without_payload(
    tmp_path,
):
    root=(tmp_path/"diagnosis").resolve()
    async def failed_source(directory,sid,index):
        exc=SpotTransportError(
            "do not persist public payload or request address",
            reason="QUEUE_BACKPRESSURE",
        )
        exc.ingress_queue_high_watermark=127
        raise exc

    result=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,
        attempts=1,max_frames=20,timeout_s=20,
        capture_factory=failed_source,
    )
    assert result.failed_sessions==1
    assert result.max_queue_high_watermark==127
    assert result.open_outages==1
    with sqlite3.connect(root/"forward_supervisor.sqlite") as db:
        row=db.execute("""
            SELECT status,failure_code,queue_high_watermark,
                   recorded_storage_bytes FROM forward_watch_attempt
        """).fetchone()
        assert row[0]=="FAILED"
        assert row[1]=="SPOT_TRANSPORT_QUEUE_BACKPRESSURE"
        assert row[2]==127
        assert row[3]>0
        reason=db.execute("""
            SELECT last_failure_code FROM forward_watch_outage
        """).fetchone()[0]
        assert reason=="SPOT_TRANSPORT_QUEUE_BACKPRESSURE"
        assert "payload" not in reason


@pytest.mark.asyncio
async def test_existing_nonmarket_errors_keep_same_type_names_for_prior_regressions(
    tmp_path,
):
    root=(tmp_path/"generic").resolve()
    async def failed_source(directory,sid,index):
        raise ConnectionResetError("sensitive IP")

    result=await run_forward_research_supervisor(
        output_dir=root,allow_public_network=True,attempts=1,
        max_frames=20,timeout_s=20,capture_factory=failed_source,
    )
    assert result.failed_sessions==1
    with sqlite3.connect(root/"forward_supervisor.sqlite") as db:
        assert db.execute(
            "SELECT failure_code FROM forward_watch_attempt"
        ).fetchone()[0]=="ConnectionResetError"


def test_operator_long_trials_refuse_low_memory_before_creating_evidence(tmp_path,monkeypatch):
    from ci import phase1_operator_lab as lab
    monkeypatch.setattr(lab,"_memory",lambda:(8*2**30,400*2**20))
    base=(tmp_path/"not_created").resolve()
    with pytest.raises(RuntimeError,match="1.5 GiB"):
        lab.run_smoke(allow_public=True,profile="hour",evidence_base=base)
    assert not base.exists()


def test_operator_ram_samples_and_failed_queue_are_in_readonly_report(tmp_path):
    from ci import phase1_operator_lab as lab
    root=(tmp_path/"old_style_trial").resolve()
    (root/"evidence").mkdir(parents=True)
    with sqlite3.connect(root/"evidence"/"forward_supervisor.sqlite") as db:
        db.execute("""CREATE TABLE forward_watch_plan(
            planned_watch_duration_s REAL, attempts_ceiling INTEGER,
            storage_ceiling_bytes INTEGER, admitted_at_ms INTEGER,
            completed_at_ms INTEGER, completion_code TEXT)""")
        db.execute(
            "INSERT INTO forward_watch_plan VALUES (3600,10,100000000,1792320000000,NULL,NULL)"
        )
        db.execute("""CREATE TABLE forward_watch_attempt(
            iteration INTEGER,session_id TEXT,status TEXT,
            failure_code TEXT,originals INTEGER,receiver_acks INTEGER,
            queue_high_watermark INTEGER,receiver_process_starts INTEGER,
            max_receiver_ack_latency_ms REAL)""")
        db.execute("""
            INSERT INTO forward_watch_attempt VALUES
            (1,'rec_demo','FAILED','SPOT_TRANSPORT_QUEUE_BACKPRESSURE',
             0,0,128,0,0)
        """)
        db.execute("""CREATE TABLE forward_watch_outage(
            outage_id INTEGER PRIMARY KEY,opened_ms INTEGER,
            restored_ms INTEGER,last_failure_code TEXT,
            coverage_verified INTEGER)""")
        db.execute("""
            INSERT INTO forward_watch_outage VALUES
            (1,1792320000000,NULL,'SPOT_TRANSPORT_QUEUE_BACKPRESSURE',0)
        """)
        db.execute("""CREATE TABLE forward_watch_unobserved_boundary(
            boundary_id INTEGER PRIMARY KEY,
            previous_iteration INTEGER,next_iteration INTEGER,
            coverage_verified INTEGER)""")
    (root/"host_resources.jsonl").write_text(
        "\\n".join([
            '{"available_ram_gib":1.3}',
            '{"available_ram_gib":0.6}',
        ])+"\\n",
        encoding="utf-8",
    )
    report=lab.inspect_trial(root)
    assert report["failure_classes"]=={"SPOT_TRANSPORT_QUEUE_BACKPRESSURE":1}
    assert report["failed_attempt_diagnostics"][0]["source_queue_high_watermark"]==128
    assert report["max_queue_high_watermark"]==128
    assert report["operator_host_ram_min_available_gib"]==0.6
    assert report["operator_host_resource_samples"]==2
    assert report["gap_free_history_verified"] is False
