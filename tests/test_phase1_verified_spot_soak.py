"""Supervised finite verified Spot soak: actual chain, checkpoint, crash recovery."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from ci.phase1_verified_spot_soak import main, parser, validate
from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_public_capture_adapter import (
    SpotTransportResult, SpotTransportError,
)
import infrastructure.spot_verified_soak as supervisor
from infrastructure.spot_verified_soak import (
    VerifiedSpotSoakError, run_verified_spot_soak,
)

WS = "wss://data-stream.binance.vision/stream"
REST = "https://data-api.binance.vision/api/v3/depth"


def ws(payload):
    stream = "btcusdt@aggTrade" if payload["e"] == "aggTrade" else "btcusdt@depth@100ms"
    return json.dumps({"stream":stream, "data":payload}, separators=(",", ":")).encode()


def capture_factory(round_number: int, root: Path):
    # Deliberately fixture-only. No network or credentials, but the ENTIRE
    # original -> sealed replay -> HMAC subprocess ACK -> named derived
    # consumer chain is exercised (not a mocked acceptance report).
    async def capture(session_id: str, sequence: int) -> SpotTransportResult:
        now = time.time_ns() // 1_000_000
        journal = OfflineSpotCaptureJournal(
            root / "original_public.sqlite",
            mode_provider=lambda: QuantMode.SIMULATION,
        )
        journal.start_session(
            session_id, symbol="BTCUSDT", source="offline_fixture",
            ws_endpoint=WS, opened_at_ms=now,
        )
        for n in (500, 501):
            if n == 501:
                journal.record_ws(
                    session_id,
                    ws({
                        "e":"depthUpdate", "E":now, "s":"BTCUSDT",
                        "U":100, "u":102, "b":[["100","4"]], "a":[],
                    }),
                    stream_kind="depthUpdate", received_at_ms=now,
                )
                journal.record_snapshot(
                    session_id, json.dumps({
                        "lastUpdateId":100, "bids":[["100","3"]],
                        "asks":[["102","3"]],
                    }).encode(), rest_endpoint=REST, request_symbol="BTCUSDT",
                    request_started_at_ms=now, received_at_ms=now,
                )
            journal.record_ws(
                session_id,
                ws({
                    "e":"aggTrade", "s":"BTCUSDT", "a":n, "E":now,
                    "T":now - 10, "f":n, "l":n, "p":"101",
                    "q":"0.01", "m":False,
                }),
                stream_kind="aggTrade", received_at_ms=now,
            )
        journal.close_session(session_id, closed_at_ms=now)
        return SpotTransportResult(session_id, "BTCUSDT", 3, 1, now, 1, "SYNCED", 102)
    return capture


@pytest.mark.asyncio
async def test_three_real_offline_source_to_receiver_acks_sealed_rounds(tmp_path):
    root = tmp_path / "verified_supervisor"
    state = await run_verified_spot_soak(
        output_dir=root, allow_network=True, rounds=3, max_frames=20,
        capture_factory=capture_factory,
    )
    assert state["status"] == "COMPLETE"
    assert state["fixture"] is True
    assert len(state["rounds"]) == 3
    assert len({row["session_id"] for row in state["rounds"]}) == 3
    assert state["continuous_live_market"] is False
    assert state["history_continuity_verified"] is False
    assert state["orders_submitted"] == 0
    for i, row in enumerate(state["rounds"], start=1):
        assert row["round"] == i
        assert row["verified_trades"] == row["source_acks"] == row["receiver_receipts"] == 2
        assert row["derived_receipts"] == 5
        assert row["named_receipts"] == {
            "research_audit":5, "research_monitor":5,
        }
        assert row["transport_scope"] == "OFFLINE_FIXTURE"
        assert (root / f"round_{i:03d}" / "original_public.sqlite").exists()
    stored = json.loads((root / "verified_soak_manifest.json").read_text())
    assert stored == state
    # A completed valid scope can be read without retriggering capture.
    assert await run_verified_spot_soak(
        output_dir=root, allow_network=True, resume=True,
        rounds=3, max_frames=20, capture_factory=capture_factory,
    ) == state


@pytest.mark.asyncio
async def test_safe_checkpoint_restart_only_after_completed_round(tmp_path, monkeypatch):
    root = tmp_path / "restart"
    original = supervisor.observe_verified_public_batches
    calls = {"count":0}

    async def interrupt_before_next_source(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("controlled interruption before next source opens")
        return await original(*args, **kwargs)

    monkeypatch.setattr(supervisor, "observe_verified_public_batches", interrupt_before_next_source)
    with pytest.raises(RuntimeError, match="controlled interruption"):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=2, max_frames=20,
            capture_factory=capture_factory,
        )
    state = json.loads((root/"verified_soak_manifest.json").read_text())
    assert state["status"] == "RUNNING"
    assert len(state["rounds"]) == 1
    assert not (root/"round_002").exists()
    monkeypatch.setattr(supervisor, "observe_verified_public_batches", original)
    recovered = await run_verified_spot_soak(
        output_dir=root, allow_network=True, resume=True,
        rounds=2, max_frames=20, capture_factory=capture_factory,
    )
    assert recovered["status"] == "COMPLETE"
    assert len(recovered["rounds"]) == 2
    assert recovered["rounds"][0] == state["rounds"][0]


@pytest.mark.asyncio
async def test_partial_failed_round_quarantines_evidence_on_resume(tmp_path):
    root = tmp_path / "incomplete"
    def factory(index, path):
        if index == 2:
            async def broken(sid, seq):
                raise SpotTransportError("intentionally disconnected", reason="WS_READ_FAILED")
            return broken
        return capture_factory(index, path)
    with pytest.raises(Exception):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=2,
            max_frames=20, max_attempts=1, capture_factory=factory,
        )
    assert (root / "round_002").exists()
    assert json.loads((root/"verified_soak_manifest.json").read_text())["status"] == "RUNNING"
    with pytest.raises(VerifiedSpotSoakError, match="quarantine"):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=2,
            max_frames=20, resume=True, capture_factory=capture_factory,
        )
    assert (root / "round_002").exists()  # NEVER overwrite originals


@pytest.mark.asyncio
async def test_tampered_checkpoint_source_receipts_prevent_recovery(tmp_path, monkeypatch):
    root = tmp_path / "tamper"
    original = supervisor.observe_verified_public_batches
    calls = 0
    async def pause_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("halt")
        return await original(*args, **kwargs)
    monkeypatch.setattr(supervisor, "observe_verified_public_batches", pause_after_first)
    with pytest.raises(RuntimeError):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=2,
            max_frames=20, capture_factory=capture_factory,
        )
    saved = root/"round_001"/"cycle_01"/"chain_acceptance.json"
    report = json.loads(saved.read_text())
    report["producer_ack_count"] = 987
    saved.write_text(json.dumps(report))
    with pytest.raises(VerifiedSpotSoakError, match="tampered"):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=2,
            max_frames=20, resume=True, capture_factory=capture_factory,
        )


def test_soak_cli_refuses_network_and_unbounded_policy_without_artifacts(tmp_path, capsys):
    root = tmp_path / "denied"
    assert main(["--output-dir",str(root)]) == 2
    assert "PermissionError" in capsys.readouterr().err
    assert not root.exists()
    for extra in (
        ["--rounds","25"], ["--rounds","0"], ["--max-frames","5000"],
        ["--rounds","24","--max-frames","2001"],
        ["--timeout-s","999"], ["--pause-s","61"],
    ):
        options=parser().parse_args(
            ["--output-dir",str(root),"--allow-public-market-data",*extra],
        )
        with pytest.raises(ValueError):
            validate(options)
    assert not root.exists()


@pytest.mark.asyncio
async def test_real_network_entry_without_clock_provider_fails_before_files(tmp_path):
    root = tmp_path/"requires_clock"
    with pytest.raises(VerifiedSpotSoakError, match="clock"):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=1,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_live_provenance_cannot_be_misreported_by_fixture(tmp_path, monkeypatch):
    original = supervisor.observe_verified_public_batches
    async def dishonest(*args, **kwargs):
        output=await original(*args, **kwargs)
        output["public_tls_peer_confirmed"]=True  # deliberate fake
        return output
    monkeypatch.setattr(supervisor, "observe_verified_public_batches", dishonest)
    root=tmp_path/"spoofed_tls"
    with pytest.raises(VerifiedSpotSoakError, match="unsafe"):
        await run_verified_spot_soak(
            output_dir=root, allow_network=True, rounds=1,
            max_frames=20, capture_factory=capture_factory,
        )
    assert json.loads((root/"verified_soak_manifest.json").read_text())["rounds"] == []
