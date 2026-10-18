"""Durable forward outbox crash/replay tests: source/receiver separation."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import time

import pytest

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope,
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox, ForwardOutboxError
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_spot_forward_verified_research import source, snapshot, _NOW


def envelope(capture_id=2, *, sid="rec_outbox01", price="101"):
    projection = {
        "session_id":sid,
        "source_capture_id":capture_id,
        "source_record_id":f"{sid}:aggTrade:{capture_id}",
        "topic":"md.aggTrade",
        "source_available_at_ms":1780000000000,
        "source_raw_sha256":"e"*64,
        "verified_asof_book_id":None,
        "full_verified_frame":{"p":price},
    }
    body,digest=OfflineMarketProjectionCursor._serialize(projection)
    return ProjectionEnvelope(
        "forward_spot","read_only_market",capture_id,
        f"md:forward:{sid}:{capture_id}",1780000000000,body,digest,
    )


def open_outbox(tmp_path, *, sid="rec_outbox01"):
    return OfflineForwardSpotOutbox(
        (tmp_path/"forward.sqlite").resolve(),session_id=sid,
        mode_provider=lambda:QuantMode.SIMULATION,
    )


def test_durable_stage_idempotence_and_source_identity(tmp_path):
    led=open_outbox(tmp_path)
    e=envelope()
    a=led.stage(e)
    assert a.status=="STAGED"
    assert led.stage(e)==a
    assert len(open_outbox(tmp_path).items())==1
    with pytest.raises(ForwardOutboxError,match="conflicting"):
        led.stage(envelope(price="999"))
    with pytest.raises(ForwardOutboxError,match="identity"):
        open_outbox(tmp_path,sid="rec_another")
    with pytest.raises(ForwardOutboxError,match="ACK"):
        led.ack_derived(e)
    assert led.items()[0].status=="STAGED"


def test_receiver_ack_then_derived_ack_monotonic_and_immutable(tmp_path):
    led=open_outbox(tmp_path)
    e=envelope()
    led.stage(e)
    recv=OfflineIndependentProjectionReceiver(
        tmp_path/"receiver.sqlite",mode_provider=lambda:QuantMode.SIMULATION,
    )
    ack=recv.accept(e,now_ms=1780000001000)
    led.ack_receiver(e,ack)
    led.ack_receiver(e,ack)
    assert led.items()[0].status=="RECEIVER_ACKED"
    led.ack_derived(e)
    led.ack_derived(e)
    led.ack_receiver(e,ack)  # may not move state backwards
    assert open_outbox(tmp_path).items()[0].status=="DERIVED_ACKED"
    with pytest.raises(ForwardOutboxError,match="conflicting"):
        led.stage(envelope(price="999"))


def test_independent_process_hard_exit_after_receiver_commit_replays_same_id(tmp_path):
    db=(tmp_path/"forward.sqlite").resolve()
    receiver=(tmp_path/"receiver.sqlite").resolve()
    program="""
import os,sys
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    ProjectionEnvelope,OfflineIndependentProjectionReceiver
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
sid='rec_outbox01'
projection={
    'session_id':sid,'source_capture_id':2,
    'source_record_id':sid+':aggTrade:2','topic':'md.aggTrade',
    'source_available_at_ms':1780000000000,
    'source_raw_sha256':'e'*64,'verified_asof_book_id':None,
    'full_verified_frame':{'p':'101'},
}
body,digest=OfflineMarketProjectionCursor._serialize(projection)
e=ProjectionEnvelope('forward_spot','read_only_market',2,
    'md:forward:'+sid+':2',1780000000000,body,digest)
out=OfflineForwardSpotOutbox(
    __import__('pathlib').Path(sys.argv[1]),session_id=sid,
    mode_provider=lambda:QuantMode.SIMULATION)
out.stage(e)
receiver=OfflineIndependentProjectionReceiver(
    sys.argv[2],mode_provider=lambda:QuantMode.SIMULATION)
receiver.accept(e,now_ms=1780000001000)
os._exit(47)
"""
    child=subprocess.run([sys.executable,"-c",program,str(db),str(receiver)],
                         capture_output=True,text=True,timeout=20)
    assert child.returncode==47,child.stderr
    ledger=open_outbox(tmp_path)
    saved=ledger.items()
    assert len(saved)==1 and saved[0].status=="STAGED"
    recv=OfflineIndependentProjectionReceiver(
        receiver,mode_provider=lambda:QuantMode.SIMULATION,
    )
    ack=recv.accept(saved[0].envelope,now_ms=1780000001000)
    assert len(recv.receipts("forward_spot","read_only_market"))==1
    ledger.ack_receiver(saved[0].envelope,ack)
    assert ledger.items()[0].status=="RECEIVER_ACKED"


@pytest.mark.asyncio
async def test_recovery_after_receiver_commit_before_producer_ack(tmp_path,monkeypatch):
    from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
    root=(tmp_path/"interrupted_receiver").resolve()
    original=OfflineForwardSpotOutbox.ack_receiver

    def cut_after_ipc(self,*args,**kwargs):
        raise RuntimeError("injected failure after independent receiver COMMIT")

    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_receiver",cut_after_ipc)
    with pytest.raises(RuntimeError,match="injected failure"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_recovery_receiver",
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,
        )
    assert open_outbox_path(root,"rec_recovery_receiver")[0].status=="STAGED"
    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_receiver",original)
    report=await recover_staged_forward_spot(root=root,session_id="rec_recovery_receiver")
    assert report.staged_envelopes==report.receiver_receipts==1
    assert report.primary_derived_receipts==1
    assert report.independent_named_receipts==2
    assert report.outbox_derived_acked==1
    assert report.source_session_closed
    assert not report.source_coverage_certified
    assert not report.session_certified
    assert report.orders_submitted==0
    again=await recover_staged_forward_spot(root=root,session_id="rec_recovery_receiver")
    assert again.forward_checkpoints==report.forward_checkpoints==1
    assert again.checkpoint_suffix_repaired==0
    assert report.checkpoint_suffix_repaired==1
    assert again.staged_envelopes==report.staged_envelopes
    assert again.receiver_receipts==report.receiver_receipts


def open_outbox_path(root,sid):
    return OfflineForwardSpotOutbox(
        root/"forward_outbox.sqlite",session_id=sid,
        mode_provider=lambda:QuantMode.SIMULATION,
    ).items()


@pytest.mark.asyncio
async def test_recovery_after_derived_db_commit_before_outbox_ack(tmp_path,monkeypatch):
    root=(tmp_path/"derived_crash").resolve()
    original=OfflineForwardSpotOutbox.ack_derived
    def cut(self,*args,**kwargs):
        raise RuntimeError("failure after derived SQLite commit")
    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_derived",cut)
    with pytest.raises(RuntimeError,match="derived SQLite"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_recovery_derived",
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,
        )
    assert open_outbox_path(root,"rec_recovery_derived")[0].status=="RECEIVER_ACKED"
    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_derived",original)
    report=await recover_staged_forward_spot(root=root,session_id="rec_recovery_derived")
    assert report.outbox_derived_acked==1
    assert report.receiver_receipts==1
    assert report.primary_derived_receipts==1
    assert report.independent_named_receipts==2


@pytest.mark.asyncio
async def test_tampered_raw_source_blocks_outbox_replay(tmp_path):
    root=(tmp_path/"tamper").resolve()
    # Abort on the second research raw event, leaving a staged first event.
    original=OfflineForwardSpotOutbox.ack_derived
    def cut(self,*args,**kwargs):
        raise RuntimeError("stop after first staged event")
    try:
        OfflineForwardSpotOutbox.ack_derived=cut
        with pytest.raises(RuntimeError):
            await observe_forward_spot_research(
                output_dir=root,session_id="rec_recovery_tamper",
                stream=source(_NOW()),snapshot_fetch=snapshot,
                max_frames=20,timeout_s=20,
            )
    finally:
        OfflineForwardSpotOutbox.ack_derived=original
    entry=open_outbox_path(root,"rec_recovery_tamper")[0]
    with sqlite3.connect(root/"original.sqlite") as db:
        db.execute(
            "UPDATE offline_spot_capture_records SET raw_blob=? WHERE capture_id=?",
            (b'{"corrupted":true}',entry.envelope.ingest_id),
        )
    with pytest.raises(Exception):
        await recover_staged_forward_spot(root=root,session_id="rec_recovery_tamper")
