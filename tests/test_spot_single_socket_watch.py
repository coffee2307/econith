"""Genuine SQLite/HMAC forward attestations over one fake leased WS object."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager

import pytest

from infrastructure.spot_public_pinned_lease import (
    PinnedPublicSpotLease,_LEASE_TOKEN,
)
from infrastructure.spot_single_socket_watch import run_single_socket_forward_watch
from tests.test_spot_forward_verified_research import frame,trade,depth,_NOW


def segment(now,first_trade):
    # First diff starts fresh REST bridge, then an independently synced
    # book and in-order aggTrade IDs. Exactly ten public wire messages.
    return [
        frame(depth(100,102,now)),
        frame(trade(first_trade,now)),
        frame(depth(103,103,now)),
        *[frame(trade(first_trade+i,now)) for i in range(1,8)],
    ]


class PublicSocketFixture:
    def __init__(self,frames):
        self.frames=list(frames)
        self.reads=0
        self.closed=False
    def __aiter__(self):
        return self
    async def __anext__(self):
        if self.closed:
            raise ConnectionResetError("forced public socket disconnect")
        if not self.frames:
            raise StopAsyncIteration
        self.reads+=1
        entry=self.frames.pop(0)
        if entry=="DISCONNECT":
            self.closed=True
            raise ConnectionResetError("forced public socket disconnect")
        if self.reads in (2,12):
            await asyncio.sleep(0.16)  # REST snapshot COMMIT before trades
        return entry


class PublicRestFixture:
    def __init__(self):
        self.snapshots=0
    async def get(self,url,params):
        assert params=={"symbol":"BTCUSDT","limit":1000}
        self.snapshots+=1
        return type("Reply",(),{
            "status_code":200,
            "content":json.dumps({
                "lastUpdateId":100,
                "bids":[["100","3"]],"asks":[["102","5"]],
            }).encode("utf-8"),
        })()


def lease_factory_for(sockets,connections,*,snapshot_log):
    @asynccontextmanager
    async def factory(*,allow_public_market_data):
        assert allow_public_market_data
        socket=sockets.pop(0)
        rest=PublicRestFixture()
        connections.append((socket,rest))
        lease=PinnedPublicSpotLease(
            _token=_LEASE_TOKEN,socket=socket,
            rest_client=rest,max_body_bytes=1000000,
        )
        try:
            yield lease
        finally:
            snapshot_log.append(rest.snapshots)
            lease.invalidate()
    return factory


@pytest.mark.asyncio
async def test_same_public_socket_produces_two_sealed_sessions_and_hmac_checkpoints(tmp_path):
    now=_NOW()
    socket=PublicSocketFixture(segment(now,500)+segment(now,2500))
    connections=[]
    snapshots=[]
    preflights=[]
    async def clock_check():
        preflights.append(True)
        return True
    report=await run_single_socket_forward_watch(
        output_dir=(tmp_path/"same_socket").resolve(),
        allow_public_market_data=True,attempts=2,
        max_frames=10,timeout_s=20,
        clock_check=clock_check,
        _lease_factory=lease_factory_for(
            [socket],connections,snapshot_log=snapshots,
        ),
    )
    sup=report.supervisor
    assert sup.attempts==sup.accepted_sessions==2
    assert sup.failed_sessions==0
    assert sup.total_receiver_receipts>0
    assert sup.receiver_process_starts==2
    assert sup.unobserved_session_boundaries==1
    assert sup.history_continuity_verified is False
    assert report.public_socket_starts==1
    assert report.successful_socket_reuses==1
    assert report.discarded_broken_sockets==0
    assert report.no_unobserved_market_gaps_certified is False
    assert report.public_tls_lease_established_by_trusted_connector is False
    assert socket.reads==20
    assert snapshots==[2]  # fresh REST book per independently sealed segment
    assert preflights==[True,True]


@pytest.mark.asyncio
async def test_failed_segment_discards_connection_and_restores_only_fresh_snapshot(tmp_path):
    now=_NOW()
    first=PublicSocketFixture(
        segment(now,500)+[
            frame(depth(100,102,now)),
            frame(trade(3000,now)),
            "DISCONNECT",
        ]
    )
    second=PublicSocketFixture(segment(now,9000))
    connections=[]
    snapshots=[]
    async def clock_check():return True
    report=await run_single_socket_forward_watch(
        output_dir=(tmp_path/"renew").resolve(),
        allow_public_market_data=True,attempts=3,
        max_frames=10,timeout_s=20,
        clock_check=clock_check,
        _lease_factory=lease_factory_for(
            [first,second],connections,snapshot_log=snapshots,
        ),
    )
    sup=report.supervisor
    assert sup.accepted_sessions==2
    assert sup.failed_sessions==1
    assert sup.outage_windows==1
    assert sup.open_outages==0
    assert sup.unobserved_session_boundaries==2
    assert report.public_socket_starts==2
    assert report.discarded_broken_sockets==1
    assert first.closed
    assert snapshots==[2,1]
    assert sup.history_continuity_verified is False
    assert sup.orders_submitted==0


@pytest.mark.asyncio
async def test_disallows_unauthorized_single_socket_connection(tmp_path):
    with pytest.raises(PermissionError,match="opt-in"):
        await run_single_socket_forward_watch(
            output_dir=(tmp_path/"unauthorized").resolve(),
            clock_check=lambda:None,
        )
    with pytest.raises(PermissionError,match="clock"):
        await run_single_socket_forward_watch(
            output_dir=(tmp_path/"no_clock").resolve(),
            allow_public_market_data=True,
        )
    assert not (tmp_path/"unauthorized").exists()
    assert not (tmp_path/"no_clock").exists()


@pytest.mark.asyncio
async def test_fake_lease_type_rejected_before_any_forward_acceptance(tmp_path):
    @asynccontextmanager
    async def fake_factory(*,allow_public_market_data):
        yield object()
    async def clock_check():return True
    result=await run_single_socket_forward_watch(
        output_dir=(tmp_path/"refuse").resolve(),
        allow_public_market_data=True,clock_check=clock_check,
        attempts=1,max_frames=10,timeout_s=20,
        _lease_factory=fake_factory,
    )
    assert result.supervisor.accepted_sessions==0
    assert result.supervisor.failed_sessions==1
    assert result.public_socket_starts==0
    assert result.public_tls_lease_established_by_trusted_connector is False


@pytest.mark.asyncio
async def test_diagnostic_fresh_public_socket_per_segment_never_reuses_unobserved_frames(
    tmp_path,
):
    now=_NOW()
    sockets=[
        PublicSocketFixture(segment(now,500)),
        PublicSocketFixture(segment(now,3500)),
    ]
    connections=[]
    snapshots=[]
    async def clock_check():return True
    status=await run_single_socket_forward_watch(
        output_dir=(tmp_path/"fresh_diagnostic").resolve(),
        allow_public_market_data=True,
        attempts=2,max_frames=10,timeout_s=20,
        fresh_socket_per_segment=True,clock_check=clock_check,
        _lease_factory=lease_factory_for(sockets,connections,snapshot_log=snapshots),
    )
    assert status.supervisor.accepted_sessions==2
    assert status.supervisor.failed_sessions==0
    assert status.public_socket_starts==2
    assert status.successful_socket_reuses==0
    assert status.discarded_broken_sockets==0
    assert status.supervisor.unobserved_session_boundaries==1
    assert status.no_unobserved_market_gaps_certified is False
    assert snapshots==[1,1]
    assert [sock.reads for sock,_ in connections]==[10,10]
    assert all(sock.closed is False for sock,_ in connections)


@pytest.mark.asyncio
async def test_failed_clock_preflight_closes_previous_socket_and_does_not_replay_stale_queued_frames(
    tmp_path,
):
    import httpx
    now=_NOW()
    # Without the fix, iteration 3 consumes 10 old frames from socket 1
    # after iteration 2's failed HTTP clock check, and incorrectly attempts
    # to reuse a lease left open while nobody was reading from its WSS.
    first=PublicSocketFixture(
        segment(now,500)+segment(now-20000,2500)
    )
    second=PublicSocketFixture(segment(now,9000))
    sockets=[first,second]
    connections=[]
    snapshots=[]
    calls={"clock":0}
    async def clock_check():
        calls["clock"]+=1
        if calls["clock"]==2:
            raise httpx.ConnectTimeout("public time endpoint unavailable")
        return True
    status=await run_single_socket_forward_watch(
        output_dir=(tmp_path/"clock_outage").resolve(),
        allow_public_market_data=True,attempts=3,
        max_frames=10,timeout_s=20,
        clock_check=clock_check,
        _lease_factory=lease_factory_for(sockets,connections,snapshot_log=snapshots),
    )
    assert calls["clock"]==3
    assert status.supervisor.accepted_sessions==2
    assert status.supervisor.failed_sessions==1
    assert status.supervisor.open_outages==0
    assert status.supervisor.outage_windows==1
    assert status.supervisor.unobserved_session_boundaries==2
    assert status.public_socket_starts==2
    assert status.successful_socket_reuses==0
    assert status.discarded_broken_sockets==1
    assert first.reads==10  # the stale remainder was NEVER read
    assert second.reads==10
    assert snapshots==[1,1]
    with sqlite3.connect(
        (tmp_path/"clock_outage"/"forward_supervisor.sqlite")
    ) as db:
        failures=db.execute("""
            SELECT failure_code FROM forward_watch_attempt
            WHERE status='FAILED'
        """).fetchall()
    assert failures==[("SPOT_TRANSPORT_CLOCK_PREFLIGHT_CONNECT_TIMEOUT",)]


def test_fresh_socket_cli_requires_explicit_pinned_persistent_public_mode(tmp_path):
    from ci.phase1_forward_endurance_trial import parser,validate
    base=["--allow-public-market-data","--output-dir",str((tmp_path/"e").resolve()),
          "--attempts","2","--max-frames","20"]
    invalid=parser().parse_args(base+["--fresh-socket-per-segment"])
    with pytest.raises(ValueError,match="unsafe"):
        validate(invalid)
    valid=parser().parse_args(
        base+["--persistent-receiver","--single-socket","--fresh-socket-per-segment"]
    )
    validate(valid)
    assert valid.fresh_socket_per_segment is True
