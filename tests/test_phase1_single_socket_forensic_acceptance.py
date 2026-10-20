"""P1-05: independent forensic original audit refuses self-consistent forged sources.

Fixtures use offline fake Spot frames and local SQLite; no public Binance I/O,
broker credentials, model calls or order execution.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from ci.phase1_single_socket_duration_trial import run_trial
from ci.phase1_single_socket_duration_audit import inspect as inspect_original
from ci.phase1_single_socket_forensic_acceptance import inspect, PASS, main as audit_cli


async def preflight(**kw):
    assert kw == {"max_abs_offset_ms": 3000, "max_rtt_ms": 2000}
    return SimpleNamespace(round_trip_ms=9, measured_offset_ms=1)


def raw_frame(number, *, depth=False):
    ms = time.time_ns() // 1_000_000
    if depth:
        event = {
            "e": "depthUpdate", "E": ms, "s": "BTCUSDT",
            "U": 100 if number == 1 else 103,
            "u": 102 if number == 1 else 104,
            "b": [["100", "2"]], "a": [],
        }
        stream = "btcusdt@depth@100ms"
    else:
        event = {
            "e": "aggTrade", "E": ms, "T": ms, "s": "BTCUSDT",
            "a": number, "f": number, "l": number,
            "p": "100", "q": "0.02", "m": False,
        }
        stream = "btcusdt@aggTrade"
    return json.dumps({"stream": stream, "data": event}).encode()


@asynccontextmanager
async def fake_lease(*, allow_public_market_data):
    assert allow_public_market_data is True

    class Lease:
        @property
        def stream(self):
            async def frames():
                n = 0
                while True:
                    await asyncio.sleep(0.013)
                    n += 1
                    yield raw_frame(n, depth=n <= 2)
            return frames()

        async def snapshot(self, symbol, limit):
            assert symbol == "BTCUSDT" and limit == 1000
            return json.dumps({
                "lastUpdateId": 100,
                "bids": [["100", "2"], ["99", "3"]],
                "asks": [["102", "2"], ["103", "3"]],
            }).encode()

    yield Lease()


@pytest.fixture
def finite_capture(tmp_path):
    root = (tmp_path / "source").resolve()
    result = asyncio.run(run_trial(
        root, allow_public_market_data=True, duration_s=0.20,
        _fixture=True, lease_factory=fake_lease, clock_preflight=preflight,
    ))
    assert result["verdict"] == "PASSED_BOUNDED_SINGLE_SOCKET_RESEARCH_ONLY", result
    assert inspect_original(root)["verdict"] == "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    return root


def test_independent_forensic_provenance_semantics_pass(finite_capture):
    result = inspect(finite_capture)
    assert result["verdict"] == PASS
    assert result["originals"] == result["capture_intents"]
    assert result["websocket_frames"] >= 2
    assert result["rest_snapshots"] == 1
    assert result["original_payloads_semantically_checked"] == result["originals"]
    assert result["real_exchange_history_completeness_proven"] is False
    assert result["continuous_hmac_delivery_proven"] is False
    assert result["orders_submitted"] == 0
    assert audit_cli(["--root", str(finite_capture)]) == 0


def test_changed_source_stream_even_with_rehashed_original_and_intent_refused(finite_capture):
    file = finite_capture / "original.sqlite"
    with sqlite3.connect(file) as db:
        capture_id, raw = db.execute(
            "SELECT capture_id,raw_blob FROM offline_spot_capture_records "
            "WHERE kind='aggTrade' LIMIT 1"
        ).fetchone()
        payload = json.loads(raw)
        payload["stream"] = "ethusdt@aggTrade"
        updated = json.dumps(payload).encode()
        digest = hashlib.sha256(updated).hexdigest()
        db.execute(
            "UPDATE offline_spot_capture_records SET raw_blob=?,sha256=? WHERE capture_id=?",
            (updated, digest, capture_id),
        )
        db.execute(
            "UPDATE offline_forward_capture_intents SET sha256=? WHERE capture_id=?",
            (digest, capture_id),
        )
    assert inspect_original(finite_capture)["verdict"] == "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    with pytest.raises(ValueError, match="stream identity"):
        inspect(finite_capture)


def test_forged_report_session_id_rejected_even_if_old_audit_accepts(finite_capture):
    file = finite_capture / "aggregate_report.json"
    report = json.loads(file.read_text())
    report["session_id"] = "sds_fake_session"
    file.write_text(json.dumps(report))
    assert inspect_original(finite_capture)["verdict"] == "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    with pytest.raises(ValueError, match="session identity"):
        inspect(finite_capture)


def test_forged_rest_endpoint_rejected_even_if_old_audit_accepts(finite_capture):
    with sqlite3.connect(finite_capture / "original.sqlite") as db:
        db.execute(
            "UPDATE offline_spot_capture_records SET endpoint=? WHERE kind='snapshot'",
            ("https://unsafe.invalid/api/v3/depth",),
        )
    assert inspect_original(finite_capture)["verdict"] == "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    with pytest.raises(ValueError, match="snapshot endpoint"):
        inspect(finite_capture)


def test_reordered_source_receipt_clock_rejected_even_with_updated_intent(finite_capture):
    with sqlite3.connect(finite_capture / "original.sqlite") as db:
        original = db.execute(
            "SELECT capture_id,received_at_ms FROM offline_spot_capture_records "
            "ORDER BY capture_id LIMIT 1"
        ).fetchone()
        old_time = original[1]
        wrong_time = old_time - 60000
        db.execute(
            "UPDATE offline_spot_capture_records SET received_at_ms=? WHERE capture_id=?",
            (wrong_time, original[0]),
        )
        db.execute(
            "UPDATE offline_forward_capture_intents SET received_at_ms=? WHERE capture_id=?",
            (wrong_time, original[0]),
        )
    assert inspect_original(finite_capture)["verdict"] == "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    with pytest.raises(ValueError, match="receipt time"):
        inspect(finite_capture)


def test_forged_sqlite_session_cursor_is_rejected(finite_capture):
    with sqlite3.connect(finite_capture / "original.sqlite") as db:
        db.execute(
            "UPDATE offline_spot_capture_sessions SET last_receipt_ms=last_receipt_ms+1"
        )
    assert inspect_original(finite_capture)["verdict"] == "PASSED_ONE_SOURCE_ORIGINAL_INTEGRITY_ONLY"
    with pytest.raises(ValueError, match="session cursor"):
        inspect(finite_capture)


def test_missing_closed_source_session_refused(finite_capture):
    with sqlite3.connect(finite_capture / "original.sqlite") as db:
        db.execute("UPDATE offline_spot_capture_sessions SET closed_at_ms=NULL")
    with pytest.raises(ValueError):
        inspect(finite_capture)


@pytest.mark.parametrize("corruption", ["read_frames", "snapshot_count", "frame_cap", "claimed_hmac"])
def test_forged_publisher_safety_field_cannot_pass(finite_capture, corruption):
    path = finite_capture / "aggregate_report.json"
    report = json.loads(path.read_text())
    if corruption == "read_frames":
        report["read_frames"] += 1
    elif corruption == "snapshot_count":
        report["snapshot_captures"] += 1
    elif corruption == "frame_cap":
        report["frame_cap"] = 999999
    else:
        report["hmac_forward_receiver_tested"] = True
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        inspect(finite_capture)


def test_cli_requires_independent_recheck_before_success(monkeypatch, tmp_path, capsys):
    import ci.phase1_single_socket_duration_trial as producer

    async def synthetic_capture(root, **kwargs):
        return await run_trial(
            root, allow_public_market_data=True,
            duration_s=0.18, _fixture=True,
            lease_factory=fake_lease, clock_preflight=preflight,
        )

    monkeypatch.setattr(producer, "run_trial", synthetic_capture)
    healthy = (tmp_path / "cli_good").resolve()
    args = [
        "--allow-public-market-data", "--acknowledge-not-24h-proof",
        "--duration-s", "90", "--output-dir", str(healthy),
    ]
    assert producer.main(args) == 0
    assert "PASSED_PRODUCER_AND_INDEPENDENT_SOURCE_AUDIT_ONLY" in capsys.readouterr().out

    async def altered_capture(root, **kwargs):
        result = await synthetic_capture(root)
        with sqlite3.connect(root / "original.sqlite") as db:
            db.execute(
                "UPDATE offline_spot_capture_sessions SET last_receipt_ms=last_receipt_ms+1"
            )
        return result

    monkeypatch.setattr(producer, "run_trial", altered_capture)
    tampered = (tmp_path / "cli_tamper").resolve()
    args[-1] = str(tampered)
    assert producer.main(args) == 2
    output = capsys.readouterr().out
    assert "REFUSED_INDEPENDENT_SOURCE_AUDIT" in output
    assert (tampered / "original.sqlite").exists()
