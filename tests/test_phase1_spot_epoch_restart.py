"""Explicit offline source-epoch restart; no gap-free claim, no auto-connect."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace

import pytest

from ci.phase1_spot_epoch_restart import (
    run_offline_epoch_restart,inspect_quarantined_prefix,
)
from ci.phase1_rotating_spot_source import run_rotation
from ci.phase1_spot_epoch_audit import inspect as audit_epoch_transition


async def clock(**kw):
    assert kw=={"max_abs_offset_ms":3000,"max_rtt_ms":2000}
    return SimpleNamespace(round_trip_ms=3,measured_offset_ms=0)


def make_lease(*,broken_second_snapshot:bool):
    """New factory creates a new simulated socket and separate REST state."""
    @asynccontextmanager
    async def factory(*,allow_public_market_data):
        assert allow_public_market_data is True
        state={"last":100,"snapshots":0}
        class Socket:
            @property
            def stream(self):
                async def reader():
                    for i in range(24):
                        await asyncio.sleep(.055)
                        state["last"]+=1
                        now=time.time_ns()//1_000_000
                        body={"e":"depthUpdate","E":now,"s":"BTCUSDT",
                              "U":state["last"],"u":state["last"],
                              "b":[["100","2"]],"a":[]}
                        yield json.dumps({
                            "stream":"btcusdt@depth@100ms","data":body
                        },separators=(",",":")).encode()
                    # A real finite duration reader expires after 2.65s,
                    # without fabricating additional order-book updates.
                    while True:
                        await asyncio.sleep(3.0)
                return reader()
            async def snapshot(self,symbol,limit):
                assert symbol=="BTCUSDT" and limit==1000
                state["snapshots"]+=1
                last_id=101+12*(state["snapshots"]-1)
                if broken_second_snapshot and state["snapshots"]==2:
                    last_id=1  # Deliberately cannot bridge next U=113.
                return json.dumps({"lastUpdateId":last_id,
                    "bids":[["100","2"],["99","2"]],
                    "asks":[["102","2"],["103","2"]]}).encode()
        yield Socket()
    return factory


@pytest.mark.asyncio
async def test_failed_book_epoch_then_new_independently_audited_hmac_epoch(tmp_path):
    root=(tmp_path/"isolated_epoch_restart").resolve()
    a=make_lease(broken_second_snapshot=True)
    b=make_lease(broken_second_snapshot=False)
    result=await run_offline_epoch_restart(
        root,first_lease_factory=a,second_lease_factory=b,
        clock_preflight=clock,
        explicit_offline_operator_restart_approval=True,
    )
    assert result["verdict"]=="PASSED_OFFLINE_NEW_SOURCE_EPOCH_ISOLATION_ONLY"
    assert result["first_epoch_verified_prefix_frames"]==12
    assert result["first_epoch_unsealed_frames_not_certified"]>=12
    assert result["new_epoch_fresh_snapshots"]==2
    assert result["new_epoch_reconstructed_projections"]>0
    assert (result["new_epoch_reconstructed_projections"]
        ==result["new_epoch_receiver_receipts"]
        ==result["new_epoch_checkpoints"])
    assert result["unknown_market_coverage_between_epochs"] is True
    assert result["gap_free_between_epochs_certified"] is False
    assert result["live_public_reconnection_tested"] is False
    assert result["orders_submitted"]==0
    unknown=json.loads((root/"unknown_boundary.json").read_text())
    assert unknown["state"]=="UNKNOWN_COVERAGE_PENDING_FRESH_EPOCH"
    assert unknown["new_rest_snapshot_required"] is True
    assert unknown["previous_book_reused"] is False
    assert not (root/"epoch_0000"/".forward_producer.lock").exists()
    assert not (root/"epoch_0001"/".forward_producer.lock").exists()
    first=json.loads((root/"epoch_0000"/"aggregate_report.json").read_text())
    second=json.loads((root/"epoch_0001"/"aggregate_report.json").read_text())
    assert first["verdict"]=="FAILED_OR_QUARANTINED"
    assert first["failure_code"] in ("FRAME_LIMIT_UNSYNCED","BOOK_NOT_SYNCED")
    assert second["verdict"]=="PASSED_BOUNDED_SOURCE_ROTATION_RESEARCH_ONLY"

    independent=audit_epoch_transition(root)
    assert independent["verdict"]=="PASSED_LOCAL_EPOCH_ISOLATION_WITH_UNKNOWN_COVERAGE_ONLY"
    assert independent["previous_failed_source_prefix_frames_sha_verified"]==12
    assert independent["new_epoch_receiver_receipts"]==result["new_epoch_receiver_receipts"]
    assert independent["inter_epoch_gap_free"] is False
    # The failed epoch remains UNKNOWN even after downstream becomes healthy.
    marker=root/"unknown_boundary.json"
    original=marker.read_text()
    marker.write_text(original.replace(
        '"verified_gap_free_between_epochs": false',
        '"verified_gap_free_between_epochs": true'
    ))
    with pytest.raises(ValueError,match="UNKNOWN boundary"):
        audit_epoch_transition(root)
    marker.write_text(original)
    assert audit_epoch_transition(root)["inter_epoch_gap_free"] is False
    receipt=root/"offline_epoch_restart_evidence.json"
    evidence=receipt.read_text()
    receipt.write_text(evidence.replace(
        '"gap_free_between_epochs_certified": false',
        '"gap_free_between_epochs_certified": true'
    ))
    with pytest.raises(ValueError,match="aggregate changed"):
        audit_epoch_transition(root)


@pytest.mark.asyncio
async def test_no_operator_approval_means_no_evidence_and_no_network(tmp_path):
    root=(tmp_path/"no_consent").resolve()
    with pytest.raises(PermissionError):
        await run_offline_epoch_restart(
            root,first_lease_factory=make_lease(broken_second_snapshot=True),
            second_lease_factory=make_lease(broken_second_snapshot=False),
            clock_preflight=clock,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_healthy_first_epoch_does_not_authorize_unneeded_restart(tmp_path):
    root=(tmp_path/"no_error").resolve()
    with pytest.raises(ValueError,match="did not fail"):
        await run_offline_epoch_restart(
            root,first_lease_factory=make_lease(broken_second_snapshot=False),
            second_lease_factory=make_lease(broken_second_snapshot=False),
            clock_preflight=clock,
            explicit_offline_operator_restart_approval=True,
        )
    assert (root/"epoch_recovery_quarantine.json").is_file()
    assert not (root/"unknown_boundary.json").exists()
    assert not (root/"epoch_0001").exists()
    assert not (root/"offline_epoch_restart_evidence.json").exists()


@pytest.mark.asyncio
async def test_tampered_failed_prefix_cannot_be_recertified(tmp_path):
    root=(tmp_path/"tamper").resolve()
    result=await run_rotation(
        root,allow_public_market_data=True,
        duration_s=2.65,max_frames_per_segment=12,
        lease_factory=make_lease(broken_second_snapshot=True),
        clock_preflight=clock,_fixture=True,
    )
    assert result["verdict"]=="FAILED_OR_QUARANTINED"
    assert inspect_quarantined_prefix(root)["verified_sealed_prefix_ws_frames"]==12
    data=root/"segments"/"0000"/"original.sqlite"
    with sqlite3.connect(data) as db:
        db.execute("UPDATE offline_spot_capture_records SET sha256=? WHERE capture_id=1",
                   ("a"*64,))
    with pytest.raises(ValueError,match="SHA"):
        inspect_quarantined_prefix(root)


@pytest.mark.asyncio
async def test_failed_epoch_live_factory_not_accepted_in_offline_recovery(tmp_path):
    from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease
    root=(tmp_path/"deny_network").resolve()
    with pytest.raises(ValueError):
        await run_offline_epoch_restart(
            root,first_lease_factory=open_pinned_public_spot_lease,
            second_lease_factory=make_lease(broken_second_snapshot=False),
            clock_preflight=clock,
            explicit_offline_operator_restart_approval=True,
        )
    assert not root.exists()
