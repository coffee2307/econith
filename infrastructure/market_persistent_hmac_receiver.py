"""Explicitly opted-in bounded persistent HMAC receiver for SIMULATION research.

One independently owned Python subprocess and SQLite receiver ledger per
finite source session. Parent sends ordered per-message HMAC authenticated
envelopes; child sends sequence-bound HMAC authenticated durable receipts.
No sockets, account credentials, broker/strategy callbacks or live orders.
The host and process environment are trusted; this is NOT remote auth/TLS.

A timeout, partial ACK, replayed sequence, worker exit or cancellation
poisons this client and terminates the child. The producer outbox remains
the authority on STAGED/RECEIVER_ACKED; any committed-but-unacknowledged
receiver intent must be reconciled through the existing source-bound recovery.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from ci.phase1_authenticated_receiver_ipc import (
    AuthenticatedReceiptIPCError, _CHILD_KEY_ENV, _MAX_BATCH_SIZE,
    _MAX_WIRE_BYTES, _decode_json, _encode_json, _tagged_mac, _verify_mac,
)
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, ProjectionEnvelope, ReceiverOwnedReceipt,
)

_REQUEST = b"ECONITH-P1-PERSIST-REQUEST-v1\x00"
_RESPONSE = b"ECONITH-P1-PERSIST-RESPONSE-v1\x00"
_MAX_MESSAGES = 4096


def encode_request(key: bytes, sequence: int,
                   envelopes: Sequence[ProjectionEnvelope], now_ms: int) -> bytes:
    if (
        type(sequence) is not int or not 1 <= sequence <= _MAX_MESSAGES
        or type(now_ms) is not int or now_ms <= 0
        or type(envelopes) not in (list, tuple)
        or not 1 <= len(envelopes) <= _MAX_BATCH_SIZE
        or any(type(e) is not ProjectionEnvelope for e in envelopes)
        or len({(e.source_consumer,e.receiver,e.ingest_id) for e in envelopes}) != len(envelopes)
    ):
        raise AuthenticatedReceiptIPCError("invalid bounded persistent receiver request")
    body={"sequence":sequence, "now_ms":now_ms, "envelopes":[asdict(e) for e in envelopes]}
    return _encode_json({"body":body,"mac":_tagged_mac(key,_REQUEST,body)}) + b"\n"


def decode_request(packet: bytes, key: bytes, expected_sequence: int) -> dict:
    request=_decode_json(packet)
    if set(request)!={"body","mac"} or type(request["body"]) is not dict:
        raise AuthenticatedReceiptIPCError("invalid persistent receiver frame")
    _verify_mac(key,_REQUEST,request["body"],request["mac"])
    body=request["body"]
    if (
        set(body)!={"sequence","now_ms","envelopes"}
        or body["sequence"]!=expected_sequence
        or type(body["sequence"]) is not int
        or type(body["now_ms"]) is not int or body["now_ms"]<=0
        or type(body["envelopes"]) is not list
        or not 1 <= len(body["envelopes"]) <= _MAX_BATCH_SIZE
        or any(type(e) is not dict or
               set(e)!=set(ProjectionEnvelope.__dataclass_fields__) for e in body["envelopes"])
    ):
        raise AuthenticatedReceiptIPCError("incorrect persistent receiver request sequence")
    envelopes=[ProjectionEnvelope(**entry) for entry in body["envelopes"]]
    if len({(e.source_consumer,e.receiver,e.ingest_id) for e in envelopes}) != len(envelopes):
        raise AuthenticatedReceiptIPCError("duplicate persistent source identity")
    return {"sequence":body["sequence"],"now_ms":body["now_ms"],"envelopes":envelopes}


def encode_response(key: bytes, sequence: int,
                    receipts: list[ReceiverOwnedReceipt]) -> bytes:
    body={"sequence":sequence,"receipts":[asdict(receipt) for receipt in receipts]}
    return _encode_json({"body":body,"mac":_tagged_mac(key,_RESPONSE,body)}) + b"\n"


def decode_response(packet: bytes, key: bytes, expected_sequence: int,
                    envelopes: Sequence[ProjectionEnvelope], now_ms: int
                    ) -> list[ReceiverOwnedReceipt]:
    frame=_decode_json(packet)
    if set(frame)!={"body","mac"} or type(frame["body"]) is not dict:
        raise AuthenticatedReceiptIPCError("invalid persistent receiver ACK")
    _verify_mac(key,_RESPONSE,frame["body"],frame["mac"])
    body=frame["body"]
    if (
        set(body)!={"sequence","receipts"}
        or type(body["sequence"]) is not int
        or body["sequence"]!=expected_sequence
        or type(body["receipts"]) is not list
        or len(body["receipts"])!=len(envelopes)
    ):
        raise AuthenticatedReceiptIPCError("replayed or incomplete persistent ACK")
    verified=[]
    for item,source in zip(body["receipts"],envelopes):
        if type(item) is not dict or set(item)!=set(ReceiverOwnedReceipt.__dataclass_fields__):
            raise AuthenticatedReceiptIPCError("invalid independently owned receipt fields")
        r=ReceiverOwnedReceipt(**item)
        if (
            type(r.ingest_id) is not int
            or type(r.received_at_ms) is not int
            or r.source_consumer!=source.source_consumer
            or r.receiver!=source.receiver
            or r.ingest_id!=source.ingest_id
            or r.event_id!=source.event_id
            or r.projection_fingerprint!=source.projection_fingerprint
            or not source.available_at_ms <= r.received_at_ms <= now_ms
            or type(r.receipt_id) is not str
            or not hmac.compare_digest(
                r.receipt_id, OfflineIndependentProjectionReceiver._receipt_id(source)
            )
        ):
            raise AuthenticatedReceiptIPCError("mismatched persistent durable receipt")
        verified.append(r)
    return verified


class PersistentHmacResearchReceiver:
    """One session-owned child; strict sequential send/ACK; bounded protocol."""

    def __init__(self, receiver_db: Path, *, timeout_s: float = 10):
        if (
            not isinstance(receiver_db,Path) or not receiver_db.is_absolute()
            or receiver_db.is_symlink()
            or type(timeout_s) not in (float,int)
            or not 1 <= timeout_s <= 30
        ):
            raise ValueError("invalid local persistent receiver scope")
        self.path=receiver_db.resolve()
        self.timeout_s=timeout_s
        self._process: asyncio.subprocess.Process | None=None
        self._key=secrets.token_bytes(32)
        self._sequence=0
        self._lock=asyncio.Lock()
        self._poisoned=False
        self.process_starts=0

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    async def start(self) -> None:
        if self._poisoned or self._process is not None:
            raise AuthenticatedReceiptIPCError("persistent receiver already used")
        env=dict(os.environ)
        env[_CHILD_KEY_ENV]=self._key.hex()
        try:
            self._process=await asyncio.create_subprocess_exec(
                sys.executable,"-m","ci.phase1_persistent_receiver_worker",
                "--child","--receiver-db",str(self.path),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,env=env,
                limit=_MAX_WIRE_BYTES+8,
            )
            self.process_starts=1
        except OSError as exc:
            self._poisoned=True
            raise AuthenticatedReceiptIPCError("persistent receiver failed to start") from exc

    async def accept_batch(self, envelopes: Sequence[ProjectionEnvelope],
                           *, now_ms: int | None=None
                           ) -> list[ReceiverOwnedReceipt]:
        if self._poisoned or self._process is None:
            raise AuthenticatedReceiptIPCError("persistent receiver not available")
        async with self._lock:
            process=self._process
            if process.returncode is not None:
                self._poisoned=True
                raise AuthenticatedReceiptIPCError("persistent receiver exited")
            current=max(time.time_ns()//1_000_000,
                        max((e.available_at_ms for e in envelopes),default=0)
                        ) if now_ms is None else now_ms
            seq=self._sequence+1
            packet=encode_request(self._key,seq,envelopes,current)
            try:
                if process.stdin is None or process.stdout is None:
                    raise AuthenticatedReceiptIPCError("missing receiver IPC pipes")
                process.stdin.write(packet)
                await asyncio.wait_for(process.stdin.drain(),timeout=self.timeout_s)
                response=await asyncio.wait_for(process.stdout.readline(),timeout=self.timeout_s)
                if not response.endswith(b"\n") or len(response)>=_MAX_WIRE_BYTES:
                    raise AuthenticatedReceiptIPCError("receiver ACK missing or oversized")
                receipts=decode_response(
                    response.rstrip(b"\r\n"),self._key,seq,envelopes,current
                )
                self._sequence=seq
                return receipts
            except BaseException as exc:
                # A child may already have committed. Never invent ACK.
                self._poisoned=True
                if process.returncode is None:
                    process.kill()
                if isinstance(exc,asyncio.CancelledError):
                    raise
                raise AuthenticatedReceiptIPCError(
                    "persistent receiver ACK uncertain; source outbox requires recovery"
                ) from exc

    async def close(self) -> None:
        self._poisoned=True
        p=self._process
        if p is None:
            return
        try:
            if p.stdin is not None:
                p.stdin.close()
            await asyncio.wait_for(p.wait(),timeout=3)
        except (asyncio.TimeoutError,BrokenPipeError,ConnectionError):
            if p.returncode is None:
                p.kill()
            await p.wait()
        finally:
            self._process=None
            self._key=b"\x00"*32

    async def __aenter__(self) -> "PersistentHmacResearchReceiver":
        await self.start()
        return self

    async def __aexit__(self,*_) -> None:
        await self.close()
