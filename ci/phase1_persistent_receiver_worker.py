"""INTERNAL local research-only persistent receiver; no network service.

Exactly one independently owned receiver SQLite for an explicitly opted-in
finite SIMULATION source session. Requires a 256-bit ephemeral HMAC key in a
child-only environment variable. No stdout diagnostics or payload echoes.
Malformed framing or signature terminates the worker without an ACK.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ci.phase1_authenticated_receiver_ipc import (
    _CHILD_KEY_ENV, _MAX_WIRE_BYTES,
)
from core.mode import QuantMode
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver,
)
from infrastructure.market_persistent_hmac_receiver import (
    _MAX_MESSAGES, decode_request, encode_response,
)


def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child",action="store_true")
    parser.add_argument("--receiver-db",type=Path)
    args=parser.parse_args(argv)
    secret=os.environ.pop(_CHILD_KEY_ENV,None)
    if not args.child or args.receiver_db is None or secret is None:
        return 2
    try:
        key=bytes.fromhex(secret)
        if len(key)!=32 or not args.receiver_db.is_absolute():
            return 2
        receiver=OfflineIndependentProjectionReceiver(
            args.receiver_db,mode_provider=lambda:QuantMode.SIMULATION,
        )
        for sequence in range(1,_MAX_MESSAGES+1):
            packet=sys.stdin.buffer.readline(_MAX_WIRE_BYTES+1)
            if not packet:
                return 0  # explicit parent EOF closes a healthy worker
            if len(packet)>_MAX_WIRE_BYTES or not packet.endswith(b"\n"):
                return 2
            request=decode_request(packet.rstrip(b"\r\n"),key,sequence)
            receipts=[
                receiver.accept(e,now_ms=request["now_ms"])
                for e in request["envelopes"]
            ]
            sys.stdout.buffer.write(encode_response(key,sequence,receipts))
            sys.stdout.buffer.flush()
        # At most 4096 messages per source process. Never silently restart
        # and claim the resumed socket is the same continuous source.
        return 0
    except Exception:
        # An already-COMMITted receipt could exist; caller must recover the
        # exact immutable envelope via its producer outbox. No stderr detail.
        return 2


if __name__=="__main__":
    raise SystemExit(main())
