"""Persistent HMAC local receiver: real child process, exact ACK, crash recovery."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import signal
import subprocess
import sys

import pytest

from ci.phase1_authenticated_receiver_ipc import (
    AuthenticatedReceiptIPCError, _CHILD_KEY_ENV, _encode_json,
)
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver,
)
from infrastructure.market_persistent_hmac_receiver import (
    PersistentHmacResearchReceiver, decode_request, decode_response,
    encode_request, encode_response,
)
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_recovery import recover_staged_forward_spot
from infrastructure.spot_forward_verified_research import observe_forward_spot_research
from tests.test_phase1_authenticated_receiver_ipc import envelope
from tests.test_spot_forward_verified_research import _NOW, source, snapshot


def saved(db):
    return OfflineIndependentProjectionReceiver(
        db,mode_provider=lambda:QuantMode.SIMULATION,
    ).receipts("indicator","risk")


@pytest.mark.asyncio
async def test_one_real_receiver_pid_acks_multiple_independent_messages(tmp_path):
    db=(tmp_path/"persistent.sqlite").resolve()
    client=PersistentHmacResearchReceiver(db,timeout_s=10)
    async with client:
        pid=client.pid
        assert pid is not None
        a=await client.accept_batch([envelope(101)],now_ms=1780000000200)
        b=await client.accept_batch([envelope(102)],now_ms=1780000000300)
        repeat=await client.accept_batch([envelope(101)],now_ms=1780000000400)
        c=await client.accept_batch([envelope(103),envelope(104)],
                                    now_ms=1780000000500)
        assert client.pid==pid
        assert client.process_starts==1
        assert a==repeat
        assert len(a)==len(b)==1
        assert len(c)==2
    assert client.pid is None
    assert len(saved(db))==4
    assert [r.ingest_id for r in saved(db)]==[101,102,103,104]
    with pytest.raises(AuthenticatedReceiptIPCError,match="not available"):
        await client.accept_batch([envelope(105)])


def test_persistent_sequence_and_mac_replay_proof(tmp_path):
    key=b"r"*32
    a=envelope(101)
    wire=encode_request(key,1,[a],1780000000300)
    parsed=decode_request(wire.strip(),key,1)
    assert parsed["envelopes"]==[a]
    with pytest.raises(AuthenticatedReceiptIPCError,match="sequence"):
        decode_request(wire.strip(),key,2)
    with pytest.raises(AuthenticatedReceiptIPCError,match="unauthenticated"):
        decode_request(wire.strip(),b"z"*32,1)
    response=encode_response(key,1,[])
    with pytest.raises(AuthenticatedReceiptIPCError,match="incomplete"):
        decode_response(response.strip(),key,1,[a],1780000000300)
    with pytest.raises(AuthenticatedReceiptIPCError,match="replayed"):
        decode_response(response.strip(),key,2,[],1780000000300)
    with pytest.raises(AuthenticatedReceiptIPCError,match="invalid bounded"):
        encode_request(key,1,[a,a],1780000000300)


@pytest.mark.asyncio
async def test_killed_receiver_never_fabricates_ack_and_exact_restart_is_idempotent(tmp_path):
    db=(tmp_path/"receiver.sqlite").resolve()
    child=PersistentHmacResearchReceiver(db)
    await child.start()
    try:
        first=(await child.accept_batch([envelope(101)],
                                         now_ms=1780000000200))[0]
        assert len(saved(db))==1
        os.kill(child.pid, signal.SIGKILL)
        await asyncio.sleep(0.05)
        with pytest.raises(AuthenticatedReceiptIPCError):
            await child.accept_batch([envelope(102)],now_ms=1780000000300)
    finally:
        await child.close()
    # Fresh child has a new key, but receiver-owned SQLite remains
    # idempotent on exactly the source envelope+fingerprint.
    async with PersistentHmacResearchReceiver(db) as restarted:
        repeat=(await restarted.accept_batch([envelope(101)],
                                              now_ms=1780000000400))[0]
        assert repeat==first
        assert len(saved(db))==1


@pytest.mark.asyncio
async def test_forward_session_real_persistent_child_and_three_named_ledgers(tmp_path):
    root=(tmp_path/"forward_persistent").resolve()
    status=await observe_forward_spot_research(
        output_dir=root,session_id="rec_forward_persistent",
        stream=source(_NOW()),snapshot_fetch=snapshot,
        max_frames=20,timeout_s=20,persistent_receiver=True,
    )
    assert status.session_complete and status.all_receipts_independent
    assert status.receiver_transport=="persistent_hmac"
    assert status.receiver_process_starts==1
    assert status.max_receiver_ack_latency_ms>0
    assert status.receiver_receipts==4
    assert status.primary_derived_receipts==6
    assert status.named_derived_receipts==12
    assert status.queue_high_watermark>0
    assert not status.continuous_live_market
    assert status.orders_submitted==0
    with __import__("sqlite3").connect(root/"forward_outbox.sqlite") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM forward_outbox WHERE status='DERIVED_ACKED'"
        ).fetchone()[0]==4


@pytest.mark.asyncio
async def test_crash_between_persistent_child_commit_and_outbox_ack_recovers(tmp_path,monkeypatch):
    root=(tmp_path/"persisted_ack_crash").resolve()
    original=OfflineForwardSpotOutbox.ack_receiver

    def injected_crash(self,*args,**kwargs):
        raise RuntimeError("injected after persistent receiver commit")

    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_receiver",injected_crash)
    with pytest.raises(RuntimeError,match="injected"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_persistent_crash",
            stream=source(_NOW()),snapshot_fetch=snapshot,
            max_frames=20,timeout_s=20,persistent_receiver=True,
        )
    monkeypatch.setattr(OfflineForwardSpotOutbox,"ack_receiver",original)
    assert OfflineIndependentProjectionReceiver(
        root/"independent_receiver.sqlite",
        mode_provider=lambda:QuantMode.SIMULATION,
    ).receipts("forward_spot","read_only_market")
    report=await recover_staged_forward_spot(
        root=root,session_id="rec_persistent_crash",reconstruct_missing=True,
    )
    assert report.staged_envelopes==1
    assert report.receiver_receipts==1
    assert report.outbox_derived_acked==1
    assert report.primary_derived_receipts==1
    assert report.independent_named_receipts==2
    assert report.source_coverage_certified is False


def test_persistent_worker_without_child_authentication_does_not_start(tmp_path):
    proc=subprocess.run(
        [sys.executable,"-m","ci.phase1_persistent_receiver_worker",
         "--child","--receiver-db",str((tmp_path/"receiver.sqlite").resolve())],
        capture_output=True,timeout=10,
        env={k:v for k,v in os.environ.items() if k!=_CHILD_KEY_ENV},
    )
    assert proc.returncode==2
    assert not (tmp_path/"receiver.sqlite").exists()
