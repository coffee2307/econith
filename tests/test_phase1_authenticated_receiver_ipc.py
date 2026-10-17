"""Auth'd subprocess receiver: no sockets, no untrusted pickle, no orders."""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys

import pytest

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    CrossProcessDeliveryError, OfflineIndependentProjectionReceiver,
    ProjectionEnvelope, ReceiverOwnedReceipt,
)
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from ci.phase1_authenticated_receiver_ipc import (
    AuthenticatedReceiptIPCError,
    _ACK_TAG, _REQ_TAG,
    _accept_authenticated_input, _decode_json, _encode_json,
    _request, _tagged_mac, _verify_mac,
    accept_via_authenticated_subprocess,
)

BASE = 1_780_000_000_000
KEY = bytes.fromhex("1a" * 32)


def envelope(ingest_id=101):
    content, digest = OfflineMarketProjectionCursor._serialize(
        {"research_only": True, "price": "100.25"},
    )
    return ProjectionEnvelope(
        "indicator", "risk", ingest_id,
        f"md:binance_spot:fixture:BTCUSDT:aggTrade:{ingest_id}",
        BASE, content, digest,
    )


def receiver(tmp_path):
    return OfflineIndependentProjectionReceiver(
        tmp_path / "receiver.sqlite",
        mode_provider=lambda: QuantMode.SIMULATION,
    )


def test_authenticated_independent_process_stores_actual_receipt(tmp_path):
    event = envelope()
    receipt = accept_via_authenticated_subprocess(
        event, receiver_db=tmp_path / "receiver.sqlite",
        now_ms=BASE + 100,
    )
    assert isinstance(receipt, ReceiverOwnedReceipt)
    stored = receiver(tmp_path).receipts("indicator", "risk")
    assert stored == [receipt]
    assert receipt.receipt_id and receipt.ingest_id == event.ingest_id


def test_retry_in_fresh_process_is_idempotent_and_old_receipt_is_returned(tmp_path):
    db = tmp_path / "receipts.db"
    event = envelope()
    first = accept_via_authenticated_subprocess(
        event, receiver_db=db, now_ms=BASE + 100,
    )
    repeat = accept_via_authenticated_subprocess(
        event, receiver_db=db, now_ms=BASE + 200,
    )
    assert repeat == first
    assert len(OfflineIndependentProjectionReceiver(
        db, mode_provider=lambda: QuantMode.SIMULATION,
    ).receipts("indicator", "risk")) == 1


def test_conflicting_same_id_rejected_by_independent_worker(tmp_path):
    db = tmp_path / "receiver.sqlite"
    accept_via_authenticated_subprocess(
        envelope(), receiver_db=db, now_ms=BASE + 100,
    )
    conflicting = ProjectionEnvelope(
        "indicator", "risk", 101,
        "md:binance_spot:fixture:BTCUSDT:aggTrade:101",
        BASE, '{"price":"0"}',
        hashlib.sha256(b'{"price":"0"}').hexdigest(),
    )
    with pytest.raises(AuthenticatedReceiptIPCError, match="refused"):
        accept_via_authenticated_subprocess(
            conflicting, receiver_db=db, now_ms=BASE + 200,
        )
    assert len(OfflineIndependentProjectionReceiver(
        db, mode_provider=lambda: QuantMode.SIMULATION,
    ).receipts("indicator", "risk")) == 1


def test_mac_forgery_and_request_tampering_denied_before_sqlite_insert(tmp_path):
    target = receiver(tmp_path)
    body = _decode_json(_request(envelope(), BASE + 100, KEY).strip())
    changed = json.loads(json.dumps(body))
    changed["body"]["envelope"]["ingest_id"] = 999
    with pytest.raises(AuthenticatedReceiptIPCError, match="unauthenticated"):
        _accept_authenticated_input(_encode_json(changed), target, KEY)
    assert target.receipts("indicator", "risk") == []


def test_response_direction_signature_cannot_be_replayed_as_request(tmp_path):
    body = {"receipt": {"research_only": True}}
    signature = _tagged_mac(KEY, _ACK_TAG, body)
    with pytest.raises(AuthenticatedReceiptIPCError):
        _verify_mac(KEY, _REQ_TAG, body, signature)


def test_wrong_hmac_key_denied(tmp_path):
    receiver_obj = receiver(tmp_path)
    packet = _request(envelope(), BASE + 100, KEY)
    with pytest.raises(AuthenticatedReceiptIPCError, match="unauthenticated"):
        _accept_authenticated_input(packet.strip(), receiver_obj, b"2" * 32)
    assert receiver_obj.receipts("indicator", "risk") == []


@pytest.mark.parametrize("payload", [
    b'{"body":{},"mac":"00","body":{}}',
    b'[]', b'{"x":NaN}', b'not json', b'',
    b'{"a":1,"a":2}',
])
def test_invalid_json_wire_rejected(payload):
    with pytest.raises(AuthenticatedReceiptIPCError):
        _decode_json(payload)


@pytest.mark.parametrize("value", [b"tiny", "secret", b"", bytes(33)])
def test_bad_shared_key_size_rejected(value):
    with pytest.raises(AuthenticatedReceiptIPCError):
        _tagged_mac(value, _REQ_TAG, {"x": 1})


def test_process_without_explicit_internal_mode_cannot_start_service():
    process = subprocess.run(
        [sys.executable, "-m", "ci.phase1_authenticated_receiver_ipc"],
        capture_output=True, text=True, timeout=10,
    )
    assert process.returncode == 2
    assert "one-shot" in process.stderr
