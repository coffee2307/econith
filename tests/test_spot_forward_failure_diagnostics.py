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
