"""Bounded authenticated multi-receipt IPC: crash and forgery regression."""
from __future__ import annotations

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope,
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from ci.phase1_authenticated_receiver_ipc import (
    AuthenticatedReceiptIPCError, _ACK_TAG, _BATCH_ACK_TAG,
    _BATCH_REQ_TAG, _MAX_BATCH_SIZE,
    _accept_authenticated_batch_input, _batch_request,
    _decode_json, _encode_json, _tagged_mac,
    accept_batch_via_authenticated_subprocess,
)

BASE = 1_780_000_000_000
KEY = b"b" * 32


def envelope(n):
    serial, digest = OfflineMarketProjectionCursor._serialize(
        {"research_only": True, "price": str(n)}
    )
    return ProjectionEnvelope(
        "indicator", "risk", n,
        f"md:binance_spot:fixture:BTCUSDT:aggTrade:{n}",
        BASE, serial, digest,
    )


def reader(path):
    return OfflineIndependentProjectionReceiver(
        path, mode_provider=lambda: QuantMode.SIMULATION,
    )


def test_batch_subprocess_commits_distinct_receipts_and_replays_idempotently(tmp_path):
    db = tmp_path / "independent.sqlite"
    events = [envelope(n) for n in range(101, 105)]
    first = accept_batch_via_authenticated_subprocess(
        events, receiver_db=db, now_ms=BASE + 100,
    )
    assert len(first) == 4
    assert [r.ingest_id for r in first] == [101, 102, 103, 104]
    assert len({r.receipt_id for r in first}) == 4
    assert first == reader(db).receipts("indicator", "risk")
    replayed = accept_batch_via_authenticated_subprocess(
        events, receiver_db=db, now_ms=BASE + 200,
    )
    assert replayed == first
    assert len(reader(db).receipts("indicator", "risk")) == 4


def test_batch_request_rejects_capacity_duplicate_and_wrong_types(tmp_path):
    e = envelope(101)
    for bad in ([], [e] * 2, [e] * (_MAX_BATCH_SIZE + 1), [object()]):
        with pytest.raises(AuthenticatedReceiptIPCError):
            accept_batch_via_authenticated_subprocess(
                bad, receiver_db=tmp_path / "never-created.sqlite",
                now_ms=BASE + 100,
            )
    assert not (tmp_path / "never-created.sqlite").exists()


def test_batch_hmac_rejects_tampering_before_any_commit(tmp_path):
    r = reader(tmp_path / "private.sqlite")
    a = envelope(101)
    packet = _decode_json(_batch_request([a], BASE + 100, KEY).strip())
    packet["body"]["envelopes"][0]["ingest_id"] = 999
    with pytest.raises(AuthenticatedReceiptIPCError, match="unauthenticated"):
        _accept_authenticated_batch_input(_encode_json(packet), r, KEY)
    assert r.receipts("indicator", "risk") == []
    with pytest.raises(AuthenticatedReceiptIPCError, match="unauthenticated"):
        _accept_authenticated_batch_input(
            _batch_request([a], BASE + 100, KEY),
            r, b"c" * 32,
        )
    assert r.receipts("indicator", "risk") == []


def test_partial_receiver_commit_requires_replay_and_preserves_durable_row(tmp_path):
    r = reader(tmp_path / "private.sqlite")
    first = envelope(101)
    second = envelope(102)
    bad = ProjectionEnvelope(
        second.source_consumer, second.receiver, second.ingest_id,
        second.event_id, second.available_at_ms,
        second.projection_json, "0" * 64,
    )
    # The first SQLite COMMIT survives rejection of the second envelope;
    # without a complete authenticated response there are NO producer ACKs.
    with pytest.raises(Exception):
        _accept_authenticated_batch_input(
            _batch_request([first, bad], BASE + 100, KEY), r, KEY,
        )
    assert [e.ingest_id for e in r.receipts("indicator", "risk")] == [101]
    reply = _accept_authenticated_batch_input(
        _batch_request([first, second], BASE + 200, KEY), r, KEY,
    )
    ack = _decode_json(reply.strip())
    assert len(ack["body"]["receipts"]) == 2
    assert ack["body"]["receipts"][0]["received_at_ms"] == BASE + 100
    assert [e.ingest_id for e in r.receipts("indicator", "risk")] == [101, 102]


@pytest.mark.parametrize("spoof", ["missing", "wrong_id", "wrong_event", "future_time"])
def test_parent_denies_signed_but_incomplete_or_mismatched_receipt(
    tmp_path, monkeypatch, spoof,
):
    e = envelope(101)
    r = reader(tmp_path / "private.sqlite")
    good = r.accept(e, now_ms=BASE + 100)
    item = vars(good).copy()
    if spoof == "wrong_id":
        item["receipt_id"] = hashlib.sha256(b"fake").hexdigest()
    elif spoof == "wrong_event":
        item["event_id"] = "md:other"
    elif spoof == "future_time":
        item["received_at_ms"] = BASE + 200
    body = {"receipts": [] if spoof == "missing" else [item]}
    packet = _encode_json({
        "body": body, "mac": _tagged_mac(KEY, _BATCH_ACK_TAG, body),
    }) + b"\\n"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=packet),
    )
    with pytest.raises(AuthenticatedReceiptIPCError):
        accept_batch_via_authenticated_subprocess(
            [e], receiver_db=r.path, now_ms=BASE + 100, _key=KEY,
        )


def test_batch_and_single_request_domains_are_not_interchangeable(tmp_path):
    r = reader(tmp_path / "private.sqlite")
    packet = _decode_json(_batch_request([envelope(101)], BASE + 100, KEY))
    assert packet["mac"] == _tagged_mac(KEY, _BATCH_REQ_TAG, packet["body"])
    assert packet["mac"] != _tagged_mac(KEY, _ACK_TAG, packet["body"])


def test_batch_worker_refuses_unauthorized_invocation(tmp_path):
    p = subprocess.run(
        ["python", "-m", "ci.phase1_authenticated_receiver_ipc",
         "--batch-child", "--receiver-db", str(tmp_path / "unstarted.sqlite")],
        capture_output=True, timeout=15,
    )
    assert p.returncode == 2
    assert not (tmp_path / "unstarted.sqlite").exists()
