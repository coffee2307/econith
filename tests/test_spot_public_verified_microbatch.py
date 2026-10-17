"""Finite public verified microbatch research, fully offline simulated transport."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    SpotTransportError, SpotTransportResult,
)
from infrastructure.spot_public_verified_microbatch import (
    VerifiedPublicResearchError, observe_verified_public_batches,
)
from ci.phase1_public_verified_microbatch import main, parser, validate


WS = "wss://data-stream.binance.vision/stream"
REST = "https://data-api.binance.vision/api/v3/depth"


def frame(payload):
    stream = (
        "btcusdt@aggTrade" if payload["e"] == "aggTrade"
        else "btcusdt@depth@100ms"
    )
    return json.dumps({"stream":stream, "data":payload}).encode()


def trade(agg, now):
    return {
        "e":"aggTrade","s":"BTCUSDT","a":agg,"E":now,"T":now-10,
        "f":agg,"l":agg,"p":"101.25","q":"0.01","m":False,
    }


def depth(now):
    return {
        "e":"depthUpdate","s":"BTCUSDT","E":now,
        "U":100,"u":102,"b":[["100","4"]],"a":[],
    }


def fake_capture(root, *, fail_first=False, broken_trade=False):
    attempts = []
    async def capture(session_id, sequence):
        attempts.append((session_id,sequence))
        if fail_first and len(attempts) == 1:
            raise SpotTransportError("deliberate missing first WS", reason="WS_READ_FAILED")
        now = time.time_ns() // 1_000_000
        journal = OfflineSpotCaptureJournal(
            root/"original_public.sqlite",
            mode_provider=lambda:QuantMode.SIMULATION,
        )
        journal.start_session(
            session_id, symbol="BTCUSDT",
            source="offline_fixture",
            ws_endpoint=WS, opened_at_ms=now,
        )
        journal.record_ws(
            session_id, frame(trade(500,now)),
            stream_kind="aggTrade", received_at_ms=now,
        )
        journal.record_ws(
            session_id, frame(depth(now)),
            stream_kind="depthUpdate", received_at_ms=now,
        )
        journal.record_snapshot(
            session_id,json.dumps({
                "lastUpdateId":100,
                "bids":[["100","2"]],"asks":[["102","3"]],
            }).encode(),rest_endpoint=REST,request_symbol="BTCUSDT",
            request_started_at_ms=now,received_at_ms=now,
        )
        journal.record_ws(
            session_id, frame(trade(502 if broken_trade else 501,now)),
            stream_kind="aggTrade",received_at_ms=now,
        )
        journal.close_session(session_id,closed_at_ms=now)
        return SpotTransportResult(
            session_id,"BTCUSDT",3,1,now,1,
            "SYNCED",102,
        )
    return attempts, capture


@pytest.mark.asyncio
async def test_two_verified_public_batches_deliver_only_after_independent_ack(tmp_path):
    root = tmp_path/"verified_sessions"
    attempts, capture = fake_capture(root)
    result = await observe_verified_public_batches(
        output_dir=root,allow_network=True,cycles=2,
        max_frames=20,capture_once=capture,
    )
    assert result["acceptance"] == "PASSED_BOUNDED_PUBLIC_VERIFIED_MICROBATCH_RESEARCH"
    assert result["completed_cycles"] == 2
    assert result["source_sessions_distinct"]
    assert len(set(x[0] for x in attempts)) == 2
    assert not result["continuous_live_market"]
    assert not result["history_continuity_verified"]
    assert not result["outage_and_gap_free_stream_proven"]
    assert result["historical_replay_only"]
    assert not result["public_tls_peer_confirmed"]
    assert result["orders_submitted"] == 0
    assert not result["execution_started"]
    for i,session in enumerate(result["sessions"]):
        assert session["verified_trades"] == 2
        assert session["originals"] == 4
        assert session["producer_acks"] == 2
        assert session["receiver_receipts"] == 2
        assert session["derived"] == {
            "md.ticker":2,"indicator.volume_delta":2,"indicator.obi":1,
        }
        assert session["unobserved_between_sessions"] is (i>0)
        assert (root/f"cycle_{i+1:02d}"/"chain_acceptance.json").exists()
    progress = json.loads((root/"microbatch_progress.json").read_text())
    assert progress["completed_cycles"] == 2
    assert progress["orders_submitted"] == 0
    assert progress["history_continuity_verified"] is False


@pytest.mark.asyncio
async def test_source_reconnect_failure_then_valid_capture_is_research_only(tmp_path):
    root=tmp_path/"recovered"
    attempts,capture = fake_capture(root,fail_first=True)
    report=await observe_verified_public_batches(
        output_dir=root,allow_network=True,cycles=1,
        max_frames=20,capture_once=capture,
    )
    assert report["completed_cycles"] == 1
    assert len(attempts) == 2
    assert attempts[0][0] != attempts[1][0]
    assert not report["history_continuity_verified"]
    assert report["sessions"][0]["producer_acks"] == 2
    assert report["orders_submitted"] == 0


@pytest.mark.asyncio
async def test_failing_market_sequence_cannot_publish_derived_results(tmp_path):
    root=tmp_path/"bad_market_source"
    _,capture = fake_capture(root,broken_trade=True)
    with pytest.raises(VerifiedPublicResearchError,match="no valid public"):
        await observe_verified_public_batches(
            output_dir=root,allow_network=True,cycles=1,max_frames=20,
            max_attempts=1,capture_once=capture,
        )
    assert not (root/"microbatch_progress.json").exists()
    assert not (root/"cycle_01"/"receiver.sqlite").exists()


@pytest.mark.asyncio
async def test_missing_public_consent_never_creates_output_or_calls_capture(tmp_path):
    root=tmp_path/"no_capture"
    attempts,capture = fake_capture(root)
    with pytest.raises(PermissionError):
        await observe_verified_public_batches(
            output_dir=root,allow_network=False,capture_once=capture,
        )
    assert not root.exists()
    assert attempts == []


@pytest.mark.asyncio
async def test_refuses_to_overwrite_verified_public_original(tmp_path):
    root=tmp_path/"already_exists"
    root.mkdir()
    _,capture=fake_capture(root)
    with pytest.raises(FileExistsError):
        await observe_verified_public_batches(
            output_dir=root,allow_network=True,capture_once=capture,
        )


def test_cli_optin_and_bounds_no_network_or_db(tmp_path,capsys):
    path=tmp_path/"manual_research"
    assert main(["--output-dir",str(path)]) == 2
    assert "PermissionError" in capsys.readouterr().err
    assert not path.exists()
    for extra in (
        ["--cycles","0"],["--max-frames","5000"],
        ["--max-attempts","0"],["--timeout-s","1"],
        ["--max-clock-offset-ms","0"],
    ):
        opts=parser().parse_args(
            ["--output-dir",str(path),"--allow-public-market-data",*extra],
        )
        with pytest.raises(ValueError):
            validate(opts)
    assert not path.exists()


@pytest.mark.asyncio
async def test_missing_durable_receiver_ack_refuses_eventbus_publication(
    tmp_path, monkeypatch,
):
    import infrastructure.spot_public_verified_microbatch as worker
    root=tmp_path/"receipt_denied"
    _,capture=fake_capture(root)

    def incomplete_ack(_args):
        return {
            "acceptance":"PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK",
            "book_status":"SYNCED",
            "producer_ack_count":2,
            "independent_receiver_receipt_count":1,
            "original_trades_replayed":2,
            "lineage_manifest_entries":2,
            "cross_process_authenticated_transport":True,
            "network_requests":0,
            "orders_submitted":0,
        }

    monkeypatch.setattr(worker,"verify_chain",incomplete_ack)
    with pytest.raises(VerifiedPublicResearchError,match="durable verified"):
        await observe_verified_public_batches(
            output_dir=root,allow_network=True,cycles=1,
            max_frames=20,capture_once=capture,
        )
    assert not (root/"microbatch_progress.json").exists()
