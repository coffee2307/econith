"""Offline acceptance against saved public Spot evidence; no network or orders."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from ci.phase1_offline_real_capture_chain import main, run


BASE = 1_780_000_000_000
WS = "wss://data-stream.binance.vision/stream"
REST = "https://data-api.binance.vision/api/v3/depth"
SESSION = "rec_test_observation_01"


def frame(payload):
    stream = (
        "btcusdt@aggTrade" if payload["e"] == "aggTrade"
        else "btcusdt@depth@100ms"
    )
    return json.dumps(
        {"stream":stream, "data":payload},
        separators=(",", ":"),
    ).encode()


def agg(n):
    return {
        "e":"aggTrade", "E":BASE, "T":BASE-10, "s":"BTCUSDT",
        "a":n, "f":n, "l":n, "p":"100.1",
        "q":"0.025", "m":False,
    }


def original_db(tmp_path, *, synchronized=True, closed=True):
    path = tmp_path / "original.sqlite"
    j = OfflineSpotCaptureJournal(
        path, mode_provider=lambda: QuantMode.SIMULATION,
    )
    j.start_session(
        SESSION, symbol="BTCUSDT",
        source="binance_public_simulation",
        ws_endpoint=WS, opened_at_ms=BASE+20,
    )
    j.record_ws(
        SESSION, frame(agg(500)),
        stream_kind="aggTrade", received_at_ms=BASE+100,
    )
    if synchronized:
        j.record_ws(
            SESSION, frame({
                "e":"depthUpdate","E":BASE,"s":"BTCUSDT",
                "U":100,"u":102,"b":[["100","3"]],"a":[],
            }),
            stream_kind="depthUpdate", received_at_ms=BASE+110,
        )
        j.record_snapshot(
            SESSION, json.dumps({
                "lastUpdateId":100,
                "bids":[["100","2"]], "asks":[["102","3"]],
            }).encode(),
            rest_endpoint=REST,
            request_symbol="BTCUSDT",
            request_started_at_ms=BASE+120,
            received_at_ms=BASE+140,
        )
    j.record_ws(
        SESSION, frame(agg(501)),
        stream_kind="aggTrade", received_at_ms=BASE+150,
    )
    if closed:
        j.close_session(SESSION, closed_at_ms=BASE+200)
    return path


def test_offline_real_capture_pipeline_and_separate_receiver_ack(tmp_path, capsys):
    source = original_db(tmp_path)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    output = tmp_path / "read_only_e2e"
    result = main([
        "--source-db", str(source),
        "--output-dir", str(output),
        "--session-id", SESSION,
    ])
    assert result == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["acceptance"] == "PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK"
    assert summary["network_requests"] == 0
    assert summary["orders_submitted"] == 0
    assert summary["original_records_replayed"] == 4
    assert summary["original_trades_replayed"] == 2
    assert summary["first_promotion_admitted"] == 2
    assert summary["producer_ack_count"] == 2
    assert summary["independent_receiver_receipt_count"] == 2
    assert summary["history_continuity_verified"] is False
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with sqlite3.connect(output / "receiver.sqlite") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_independent_projection_receipts"
        ).fetchone()[0] == 2
    with sqlite3.connect(output / "verified_market.sqlite") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM offline_spot_promotion_manifest"
        ).fetchone()[0] == 2


def test_second_run_rejects_collision_without_touching_source(tmp_path, capsys):
    source = original_db(tmp_path)
    out = tmp_path / "already_built"
    assert main(["--source-db",str(source),"--output-dir",str(out),
                 "--session-id",SESSION]) == 0
    assert main(["--source-db",str(source),"--output-dir",str(out),
                 "--session-id",SESSION]) == 2
    assert "FileExistsError" in capsys.readouterr().err


def test_open_session_is_refused_before_any_scratch_files(tmp_path, capsys):
    source = original_db(tmp_path, closed=False)
    out = tmp_path / "should_not_exist"
    assert main(["--source-db",str(source),"--output-dir",str(out),
                 "--session-id",SESSION]) == 2
    assert not out.exists()
    assert "ValueError" in capsys.readouterr().err


def test_missing_depth_snapshot_refused_before_creating_outputs(tmp_path, capsys):
    source = original_db(tmp_path, synchronized=False)
    out = tmp_path / "cannot_promote"
    assert main(["--source-db",str(source),"--output-dir",str(out),
                 "--session-id",SESSION]) == 2
    assert not out.exists()
    assert "ValueError" in capsys.readouterr().err


def test_wrong_session_id_refused_without_source_write(tmp_path, capsys):
    source = original_db(tmp_path)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    assert main(["--source-db",str(source),
                 "--output-dir",str(tmp_path/"reject"),
                 "--session-id","rec_wrong_session"]) == 2
    assert fingerprint == hashlib.sha256(source.read_bytes()).hexdigest()


def test_invalid_relative_db_path_stops_before_writing(tmp_path, capsys):
    assert main(["--source-db",str(tmp_path/"nonexistent.sqlite"),
                 "--output-dir",str(tmp_path/"out"),
                 "--session-id",SESSION]) == 2
    assert not (tmp_path/"out").exists()
