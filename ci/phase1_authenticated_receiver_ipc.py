"""Authenticated **local subprocess** research-only receiver transport.

This is a bounded one-shot pipe protocol, not a remotely available service:
each call spawns an independent Python interpreter with a separate SQLite
receiver-owned ledger. Requests and durable-receipt responses are authenticated
using HMAC-SHA256 over canonical JSON and an ephemeral 256-bit per-call secret,
delivered in the child environment (never command arguments or logs).
The source producer ACK is STILL separate, via OfflineCrossProcessProjectionSender.
No socket, no network, no strategy callback, no real orders or credentials.
The subprocess writes NO external effects beyond the receiver's research SQLite.

Security limitation: this only authenticates two trusted processes on the
same machine and assumes a trustworthy host/environment. It is NOT TLS, not a
general remote broker transport, and not a production auth infrastructure.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    CrossProcessDeliveryError,
    OfflineIndependentProjectionReceiver,
    ProjectionEnvelope,
    ReceiverOwnedReceipt,
)

_MAX_WIRE_BYTES = 1_048_576
_CHILD_KEY_ENV = "ECONITH_RESEARCH_RECEIVER_IPC_HMAC_KEY"
_REQ_TAG = b"ECONITH-P1-RECEIVER-REQUEST-v1\x00"
_ACK_TAG = b"ECONITH-P1-RECEIVER-RESPONSE-v1\x00"
_BATCH_REQ_TAG = b"ECONITH-P1-RECEIVER-BATCH-REQUEST-v1\x00"
_BATCH_ACK_TAG = b"ECONITH-P1-RECEIVER-BATCH-RESPONSE-v1\x00"
_MAX_BATCH_SIZE = 32


class AuthenticatedReceiptIPCError(CrossProcessDeliveryError):
    """Malformed, unauthenticated or unavailable local receiver process."""


def _encode_json(value: object) -> bytes:
    body = json.dumps(
        value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    if not 0 < len(body) < _MAX_WIRE_BYTES:
        raise AuthenticatedReceiptIPCError("IPC payload too large or empty")
    return body


def _decode_json(raw: bytes) -> dict:
    if type(raw) is not bytes or not 0 < len(raw) < _MAX_WIRE_BYTES:
        raise AuthenticatedReceiptIPCError("invalid bounded IPC message")
    try:
        def no_duplicates(pairs):
            result = {}
            for k, v in pairs:
                if k in result:
                    raise ValueError("duplicate IPC JSON key")
                result[k] = v
            return result
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=no_duplicates,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
        )
    except (UnicodeError, ValueError) as exc:
        raise AuthenticatedReceiptIPCError("invalid IPC JSON encoding") from exc
    if type(result) is not dict:
        raise AuthenticatedReceiptIPCError("IPC request must be JSON object")
    return result


def _tagged_mac(key: bytes, tag: bytes, data: object) -> str:
    if type(key) is not bytes or len(key) != 32:
        raise AuthenticatedReceiptIPCError("32-byte ephemeral IPC key required")
    return hmac.new(key, tag + _encode_json(data), hashlib.sha256).hexdigest()


def _verify_mac(key: bytes, tag: bytes, data: object, signature: object) -> None:
    if (
        type(signature) is not str or len(signature) != 64
        or not hmac.compare_digest(
            _tagged_mac(key, tag, data), signature,
        )
    ):
        raise AuthenticatedReceiptIPCError("unauthenticated local receiver message")


def _request(envelope: ProjectionEnvelope, now_ms: int, key: bytes) -> bytes:
    if not isinstance(envelope, ProjectionEnvelope):
        raise AuthenticatedReceiptIPCError("typed immutable envelope required")
    if type(now_ms) is not int or now_ms <= 0:
        raise AuthenticatedReceiptIPCError("invalid local receiver receive time")
    body = {"envelope": asdict(envelope), "now_ms": now_ms}
    return _encode_json({
        "body": body, "mac": _tagged_mac(key, _REQ_TAG, body),
    }) + b"\n"


def _accept_authenticated_input(
    packet: bytes, receiver: OfflineIndependentProjectionReceiver, key: bytes,
) -> bytes:
    source = _decode_json(packet)
    if set(source) != {"body", "mac"} or type(source["body"]) is not dict:
        raise AuthenticatedReceiptIPCError("unexpected IPC request fields")
    _verify_mac(key, _REQ_TAG, source["body"], source["mac"])
    body = source["body"]
    if set(body) != {"envelope", "now_ms"} or type(body["envelope"]) is not dict:
        raise AuthenticatedReceiptIPCError("unexpected IPC request structure")
    data = body["envelope"]
    if set(data) != set(ProjectionEnvelope.__dataclass_fields__):
        raise AuthenticatedReceiptIPCError("unexpected projection envelope fields")
    # Validation of canonical projection and source availability is done by
    # the receiver's *independent* durable SQLite transaction.
    receipt = receiver.accept(
        ProjectionEnvelope(**data), now_ms=body["now_ms"],
    )
    body_ack = {"receipt": asdict(receipt)}
    return _encode_json({
        "body": body_ack,
        "mac": _tagged_mac(key, _ACK_TAG, body_ack),
    }) + b"\n"


def accept_via_authenticated_subprocess(
    envelope: ProjectionEnvelope, *,
    receiver_db: str | Path,
    now_ms: int,
    python_executable: str | None = None,
    timeout_s: float = 15.0,
    _key: bytes | None = None,
) -> ReceiverOwnedReceipt:
    """No external socket; HMAC checked *before* ACK can reach the sender."""
    db = Path(receiver_db).resolve()
    if type(timeout_s) not in (float, int) or not 0 < timeout_s <= 60:
        raise ValueError("invalid IPC subprocess timeout")
    key = secrets.token_bytes(32) if _key is None else _key
    packet = _request(envelope, now_ms, key)
    env = dict(os.environ)
    env[_CHILD_KEY_ENV] = key.hex()
    try:
        process = subprocess.run(
            [python_executable or sys.executable, "-m",
             "ci.phase1_authenticated_receiver_ipc",
             "--child", "--receiver-db", str(db)],
            input=packet, capture_output=True, timeout=timeout_s,
            check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthenticatedReceiptIPCError(
            "independent receiver process could not confirm durable receipt"
        ) from exc
    if process.returncode != 0:
        raise AuthenticatedReceiptIPCError("receiver process refused authenticated delivery")
    frame = _decode_json(process.stdout.strip())
    if set(frame) != {"body", "mac"} or type(frame["body"]) is not dict:
        raise AuthenticatedReceiptIPCError("malformed receiver ACK response")
    _verify_mac(key, _ACK_TAG, frame["body"], frame["mac"])
    if set(frame["body"]) != {"receipt"} or type(frame["body"]["receipt"]) is not dict:
        raise AuthenticatedReceiptIPCError("unexpected receiver response structure")
    data = frame["body"]["receipt"]
    if set(data) != set(ReceiverOwnedReceipt.__dataclass_fields__):
        raise AuthenticatedReceiptIPCError("unexpected receiver receipt fields")
    return ReceiverOwnedReceipt(**data)


def _batch_request(
    envelopes: Sequence[ProjectionEnvelope], now_ms: int, key: bytes,
) -> bytes:
    # Fail before starting a process if the packet could exceed its budget.
    if type(envelopes) not in (list, tuple) or not 1 <= len(envelopes) <= _MAX_BATCH_SIZE:
        raise AuthenticatedReceiptIPCError("invalid bounded receiver batch size")
    if type(now_ms) is not int or now_ms <= 0:
        raise AuthenticatedReceiptIPCError("invalid batch receive time")
    if any(type(e) is not ProjectionEnvelope for e in envelopes):
        raise AuthenticatedReceiptIPCError("typed immutable envelopes required")
    identities = [(e.source_consumer, e.receiver, e.ingest_id) for e in envelopes]
    if len(set(identities)) != len(identities):
        raise AuthenticatedReceiptIPCError("duplicate source delivery in batch")
    body = {"envelopes": [asdict(e) for e in envelopes], "now_ms": now_ms}
    return _encode_json({
        "body": body, "mac": _tagged_mac(key, _BATCH_REQ_TAG, body),
    }) + b"\\n"


def _accept_authenticated_batch_input(
    packet: bytes, receiver: OfflineIndependentProjectionReceiver, key: bytes,
) -> bytes:
    # One signed request, separate durable receiver transaction per envelope.
    # Partial commit on a child crash is legal: without the signed complete
    # response the parent MUST NOT advance *any* source producer ACK.
    frame = _decode_json(packet)
    if set(frame) != {"body", "mac"} or type(frame["body"]) is not dict:
        raise AuthenticatedReceiptIPCError("invalid authenticated batch frame")
    _verify_mac(key, _BATCH_REQ_TAG, frame["body"], frame["mac"])
    body = frame["body"]
    if set(body) != {"envelopes", "now_ms"} or type(body["envelopes"]) is not list:
        raise AuthenticatedReceiptIPCError("invalid authenticated batch structure")
    if not 1 <= len(body["envelopes"]) <= _MAX_BATCH_SIZE:
        raise AuthenticatedReceiptIPCError("batch capacity exceeded")
    if type(body["now_ms"]) is not int or body["now_ms"] <= 0:
        raise AuthenticatedReceiptIPCError("invalid authenticated receive time")
    entries = body["envelopes"]
    if any(type(e) is not dict or set(e) != set(ProjectionEnvelope.__dataclass_fields__)
           for e in entries):
        raise AuthenticatedReceiptIPCError("unexpected batch envelope fields")
    envelopes = [ProjectionEnvelope(**e) for e in entries]
    identities = [(e.source_consumer, e.receiver, e.ingest_id) for e in envelopes]
    if len(set(identities)) != len(identities):
        raise AuthenticatedReceiptIPCError("duplicate source delivery in batch")
    receipts = [
        asdict(receiver.accept(e, now_ms=body["now_ms"])) for e in envelopes
    ]
    ack = {"receipts": receipts}
    return _encode_json({
        "body": ack, "mac": _tagged_mac(key, _BATCH_ACK_TAG, ack),
    }) + b"\\n"


def accept_batch_via_authenticated_subprocess(
    envelopes: Sequence[ProjectionEnvelope], *,
    receiver_db: str | Path,
    now_ms: int,
    python_executable: str | None = None,
    timeout_s: float = 30.0,
    _key: bytes | None = None,
) -> list[ReceiverOwnedReceipt]:
    """Bounded local batch; never producer-ACK a partial or forged response.

    A separate interpreter owns the SQLite receiver ledger. Every accepted
    envelope gets its *own* durable row and receipt; the parent verifies an
    authenticated ordered complete set before returning any receipt. If the
    child dies after one or more commits, the caller must retry the identical
    batch (SQLite admission is idempotent) rather than infer success.
    """
    if type(timeout_s) not in (float, int) or not 0 < timeout_s <= 60:
        raise ValueError("invalid batch subprocess timeout")
    key = secrets.token_bytes(32) if _key is None else _key
    packet = _batch_request(envelopes, now_ms, key)
    env = dict(os.environ)
    env[_CHILD_KEY_ENV] = key.hex()
    try:
        result = subprocess.run(
            [python_executable or sys.executable, "-m",
             "ci.phase1_authenticated_receiver_ipc",
             "--batch-child", "--receiver-db", str(Path(receiver_db).resolve())],
            input=packet, capture_output=True, timeout=timeout_s,
            check=False, env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AuthenticatedReceiptIPCError(
            "independent batch receiver could not confirm durable receipts"
        ) from exc
    if result.returncode != 0:
        raise AuthenticatedReceiptIPCError("receiver refused authenticated batch")
    response = _decode_json(result.stdout.strip())
    if set(response) != {"body", "mac"} or type(response["body"]) is not dict:
        raise AuthenticatedReceiptIPCError("malformed receiver batch ACK")
    _verify_mac(key, _BATCH_ACK_TAG, response["body"], response["mac"])
    body = response["body"]
    if set(body) != {"receipts"} or type(body["receipts"]) is not list:
        raise AuthenticatedReceiptIPCError("unexpected receiver batch ACK structure")
    if len(body["receipts"]) != len(envelopes):
        raise AuthenticatedReceiptIPCError("incomplete authenticated batch ACK")
    verified = []
    for item, source in zip(body["receipts"], envelopes):
        if type(item) is not dict or set(item) != set(ReceiverOwnedReceipt.__dataclass_fields__):
            raise AuthenticatedReceiptIPCError("invalid receiver-owned receipt fields")
        receipt = ReceiverOwnedReceipt(**item)
        if (
            type(receipt.ingest_id) is not int
            or type(receipt.received_at_ms) is not int
            or receipt.source_consumer != source.source_consumer
            or receipt.receiver != source.receiver
            or receipt.ingest_id != source.ingest_id
            or receipt.event_id != source.event_id
            or receipt.projection_fingerprint != source.projection_fingerprint
            or receipt.received_at_ms < source.available_at_ms
            or receipt.received_at_ms > now_ms
            or type(receipt.receipt_id) is not str
            or not hmac.compare_digest(
                receipt.receipt_id,
                OfflineIndependentProjectionReceiver._receipt_id(source),
            )
        ):
            raise AuthenticatedReceiptIPCError("mismatched authenticated batch receipt")
        verified.append(receipt)
    return verified


def _child_main(db: Path, *, batch: bool = False) -> int:
    secret = os.environ.pop(_CHILD_KEY_ENV, None)
    if secret is None:
        return 2
    try:
        key = bytes.fromhex(secret)
        if len(key) != 32:
            return 2
        packet = sys.stdin.buffer.readline(_MAX_WIRE_BYTES)
        if not packet.endswith(b"\n"):
            return 2
        receiver = OfflineIndependentProjectionReceiver(
            db, mode_provider=lambda: QuantMode.SIMULATION,
        )
        reply = (
            _accept_authenticated_batch_input(packet.rstrip(b"\r\n"), receiver, key)
            if batch else _accept_authenticated_input(packet.rstrip(b"\r\n"), receiver, key)
        )
        sys.stdout.buffer.write(reply)
        sys.stdout.buffer.flush()
        return 0
    except Exception:
        # Do not expose payloads, shared HMAC key, SQL/host details or paths.
        return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--batch-child", action="store_true")
    parser.add_argument("--receiver-db", type=Path)
    args = parser.parse_args(argv)
    if args.child == args.batch_child or args.receiver_db is None:
        print("This module is a one-shot internal offline receiver; no service is started.", file=sys.stderr)
        return 2
    return _child_main(args.receiver_db, batch=args.batch_child)


if __name__ == "__main__":
    raise SystemExit(main())
