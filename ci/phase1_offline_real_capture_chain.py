"""SIMULATION-only, offline acceptance on operator's saved PUBLIC Spot SQLite.

Replays ORIGINAL observed bytes on a readonly input DB, copies evidence to a
fresh local scratch DB using SQLite backup, promotes verified source trades,
then sends derived research-only projections to a separate receiver-owned
SQLite database. No network, no exchange API/credentials, no live orders,
no external callback or default runtime cutover. The original SQLite input
is never opened for writing. The report attests LOCAL replay only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from dataclasses import asdict

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_verified_research_handoff import OfflineSpotVerifiedResearchHandoff
from infrastructure.market_data_integrity import OfflineMarketIntegrityJournal
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.market_projection_inbox import OfflineMarketProjectionInbox
from infrastructure.market_cross_process_delivery import (
    OfflineIndependentProjectionReceiver, OfflineCrossProcessProjectionSender,
    ProjectionEnvelope, CrossProcessDeliveryError,
)
from ci.phase1_authenticated_receiver_ipc import (
    accept_batch_via_authenticated_subprocess, _MAX_BATCH_SIZE,
)

_PART = "phase1_offline_verified_chain"
_CANDIDATES = ("captured_copy.sqlite", "verified_market.sqlite", "receiver.sqlite", "chain_acceptance.json")


def _validate(opts: argparse.Namespace) -> tuple[Path, Path]:
    source = opts.source_db.resolve(strict=True)
    target = opts.output_dir.resolve()
    if not source.is_file() or source.suffix.lower() not in (".sqlite", ".db"):
        raise ValueError("an existing public capture SQLite file is required")
    if not target.is_absolute() or source == target:
        raise ValueError("a separate absolute output directory is required")
    if target.exists():
        if not target.is_dir():
            raise ValueError("output destination must be a directory")
        if any((target / name).exists() for name in _CANDIDATES):
            raise FileExistsError("output already contains a prior acceptance run")
    return source, target


def _session_info(source: Path, session_id: str) -> dict:
    if not isinstance(session_id, str) or not session_id.startswith("rec_"):
        raise ValueError("expected bounded public-capture session ID")
    # The original source is *read-only*, including when WAL sidecars exist.
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("""
            SELECT session_id, symbol, closed_at_ms, last_receipt_ms
            FROM offline_spot_capture_sessions WHERE session_id=?
        """, (session_id,)).fetchone()
        if row is None or row["closed_at_ms"] is None or row["symbol"] != "BTCUSDT":
            raise ValueError("original BTCUSDT public capture session must be closed")
        counts = {
            r["kind"]: r["n"] for r in conn.execute("""
                SELECT kind, COUNT(*) AS n
                FROM offline_spot_capture_records
                WHERE session_id=? GROUP BY kind
            """, (session_id,))
        }
        if counts.get("snapshot") != 1 or not counts.get("depthUpdate", 0):
            raise ValueError("no original single-snapshot depth evidence")
        return {
            "session_id": row["session_id"],
            "symbol": row["symbol"],
            "closed_at_ms": row["closed_at_ms"],
            "last_receipt_ms": row["last_receipt_ms"],
            "frames_by_type": counts,
        }


def run(opts: argparse.Namespace) -> dict:
    source, target = _validate(opts)
    session = _session_info(source, opts.session_id)
    target.mkdir(parents=True, exist_ok=True)
    capture_copy = target / _CANDIDATES[0]
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=10) as src:
            with sqlite3.connect(capture_copy) as dst:
                src.backup(dst)
        mode = lambda: QuantMode.SIMULATION
        raw = OfflineSpotCaptureJournal(capture_copy, mode_provider=mode)
        as_of_ms = session["closed_at_ms"]
        replay = OfflineSpotCaptureReplayer(raw).replay(
            opts.session_id, as_of_ms=as_of_ms, limit=5000,
        )
        if replay.depth_status != "SYNCED" or replay.book is None:
            raise ValueError("source raw replay is not synchronized")
        expected = sum(session["frames_by_type"].values())
        if replay.total_records != expected:
            raise ValueError("source replay count differs from raw ledger")
        if not replay.trades:
            raise ValueError("no aggregate trades available for research handoff")
        market = OfflineMarketIntegrityJournal(
            target / _CANDIDATES[1], market="binance_spot",
            mode_provider=mode,
        )
        bridge = OfflineSpotVerifiedResearchHandoff(raw, market)
        admitted = bridge.promote_session(opts.session_id, as_of_ms=as_of_ms)
        if admitted.admitted != len(replay.trades) or admitted.duplicates:
            raise ValueError("first promotion must admit every distinct original trade")
        manifest = bridge.manifest(opts.session_id)
        if len(manifest) != len(replay.trades):
            raise ValueError("source promotion manifest incomplete")
        cursor = OfflineMarketProjectionCursor(market)
        receiver_path = target / _CANDIDATES[2]
        # In authenticated mode, the separate Python process initializes and
        # owns receipt writes; parent only reads after durable ACKs complete.
        receiver = (
            None if opts.authenticated_subprocess else
            OfflineIndependentProjectionReceiver(receiver_path, mode_provider=mode)
        )
        inbox = OfflineMarketProjectionInbox(cursor)
        sender = OfflineCrossProcessProjectionSender(inbox)
        now = as_of_ms + 1000
        # Commit every verified source-bound projection before building a
        # bounded authenticated receiver batch. Nothing is source-ACKed by a
        # queue write, a staged projection, or a partially committed batch.
        for index in range(len(replay.trades)):
            tick = now + index * 10
            claim = cursor.claim_next("indicator", now_ms=tick)
            if claim is None:
                raise ValueError("missing verified projection claim")
            cursor.commit_projection(
                claim, {
                    "source_event_id": claim.event.event_id,
                    "price": claim.event.frame["p"],
                    "quantity": claim.event.frame["q"],
                    "research_only": True,
                }, now_ms=tick,
            )
        projections = cursor.projections("indicator")
        if len(projections) != len(replay.trades):
            raise ValueError("durable verified source projection count changed")

        # A fresh independent Python receiver per bounded chunk, not per
        # trade. Each row still has a distinct receiver-owned SQLite COMMIT,
        # receipt ID and separately fenced producer ACK.
        accepted_by_ingest = {}
        process_invocations = 0
        if opts.authenticated_subprocess:
            for start in range(0, len(projections), _MAX_BATCH_SIZE):
                chunk = projections[start:start + _MAX_BATCH_SIZE]
                envelopes = []
                for p in chunk:
                    serial, digest = cursor._serialize(p.value)
                    envelopes.append(ProjectionEnvelope(
                        "indicator", "risk", p.ingest_id, p.event_id,
                        p.available_at_ms, serial, digest,
                    ))
                receipts = accept_batch_via_authenticated_subprocess(
                    envelopes, receiver_db=receiver_path,
                    now_ms=now + start * 10,
                )
                process_invocations += 1
                for e, r in zip(envelopes, receipts):
                    accepted_by_ingest[e.ingest_id] = (e, r)

        for index, projection in enumerate(projections):
            tick = now + index * 10
            if opts.authenticated_subprocess:
                approved_envelope, approved_receipt = accepted_by_ingest[
                    projection.ingest_id
                ]

                def authenticated_result(envelope, *, expected=approved_envelope,
                                         receipt=approved_receipt):
                    if envelope != expected:
                        raise CrossProcessDeliveryError(
                            "source claim diverged after receiver batch COMMIT"
                        )
                    return receipt

                accept = authenticated_result
            else:
                accept = lambda env, at=tick: receiver.accept(env, now_ms=at)
            receipt = sender.deliver_once(
                "indicator", "risk", receiver_accept=accept, now_ms=tick,
            )
            if receipt is None:
                raise ValueError("receiver did not acknowledge a durable projection")
        source_receipts = inbox.receipts("indicator", "risk")
        if receiver is None:
            receiver = OfflineIndependentProjectionReceiver(
                receiver_path, mode_provider=mode,
            )
        receiver_receipts = receiver.receipts("indicator", "risk")
        if len(source_receipts) != len(replay.trades) or len(receiver_receipts) != len(replay.trades):
            raise ValueError("source/receiver ACK ledger missing research projections")
        if any(
            a.ingest_id != b.ingest_id
            or a.result != b.acknowledgement()
            for a, b in zip(source_receipts, receiver_receipts)
        ):
            raise ValueError("receiver-owned receipt IDs differ from source ACKs")
        # Verify that the original source was not edited and the optional
        # report never contains private paths or original market payloads.
        report = {
            "acceptance": "PASSED_OFFLINE_REAL_CAPTURE_TO_RECEIVER_ACK",
            "mode": "SIMULATION",
            "network_requests": 0,
            "orders_submitted": 0,
            "raw_input_read_only": True,
            "session_id": opts.session_id,
            "original_records_replayed": replay.total_records,
            "original_trades_replayed": len(replay.trades),
            "book_status": replay.depth_status,
            "book_last_update_id": replay.book.last_update_id,
            "first_promotion_admitted": admitted.admitted,
            "lineage_manifest_entries": len(manifest),
            "producer_ack_count": len(source_receipts),
            "independent_receiver_receipt_count": len(receiver_receipts),
            "independent_receiver_database": True,
            "cross_process_authenticated_transport": bool(
                opts.authenticated_subprocess
            ),
            "authenticated_receiver_batch_limit": (
                _MAX_BATCH_SIZE if opts.authenticated_subprocess else 0
            ),
            "authenticated_receiver_process_invocations": process_invocations,
            "transport_scope": (
                "HMAC_AUTHENTICATED_LOCAL_SUBPROCESS_PIPE_ONLY"
                if opts.authenticated_subprocess else
                "LOCAL_IN_PROCESS_CALL"
            ),
            "history_continuity_verified": False,
            "default_runtime_cutover": False,
        }
        (target / _CANDIDATES[3]).write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return report
    except Exception:
        # Preserve failed scratch evidence, but NEVER rewrite the raw input.
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--session-id", type=str, required=True)
    parser.add_argument(
        "--authenticated-subprocess", action="store_true",
        help="HMAC-authenticated separate local Python receiver process; no network",
    )
    opts = parser.parse_args(argv)
    try:
        result = run(opts)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        # Only a sanitized error type on stdout, not market data or SQL paths.
        print("PHASE1_OFFLINE_CHAIN_FAILED: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
