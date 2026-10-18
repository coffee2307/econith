"""Immutable original/atomic-intent cursor cannot skip, swap or resume silently."""
from __future__ import annotations

import sqlite3
import time

import pytest

from core.mode import QuantMode
from infrastructure.spot_ingress_capture import OfflineSpotCaptureJournal
from infrastructure.spot_projection_durable_cursor import (
    OfflineSpotProjectionCursor, ProjectionCursorError,
)
from infrastructure.spot_public_capture_adapter import _PUBLIC_WS
from tests.test_spot_forward_verified_research import frame, trade


def _new(tmp_path):
    root=tmp_path
    provider=lambda: QuantMode.SIMULATION
    journal=OfflineSpotCaptureJournal(
        root/"original.sqlite",mode_provider=provider,forward_capture_audit=True,
    )
    session="rec_cursor_fixture"
    now=time.time_ns()//1_000_000
    journal.start_session(
        session,symbol="BTCUSDT",source="offline_fixture",
        ws_endpoint=_PUBLIC_WS,opened_at_ms=now,
    )
    cursor=OfflineSpotProjectionCursor(
        root/"source_cursor.sqlite",journal=journal,session_id=session,
    )
    return journal,cursor,session,now


def test_cursor_advances_only_one_committed_source_in_order(tmp_path):
    journal,cursor,session,now=_new(tmp_path)
    for trade_id in (100,101):
        journal.record_ws(
            session,frame(trade(trade_id,now)),
            stream_kind="aggTrade",received_at_ms=now,
        )
    second=journal.verify_captured_record(session,2)
    with pytest.raises(ProjectionCursorError,match="one original"):
        cursor.advance(second)
    assert cursor.read_verified().last_capture_id==0
    first=journal.verify_captured_record(session,1)
    assert cursor.advance(first).last_capture_id==1
    assert cursor.advance(second).last_capture_id==2
    with pytest.raises(ProjectionCursorError,match="one original"):
        cursor.advance(second)


def test_cursor_detects_modified_original_or_pointer(tmp_path):
    journal,cursor,session,now=_new(tmp_path)
    journal.record_ws(
        session,frame(trade(100,now)),
        stream_kind="aggTrade",received_at_ms=now,
    )
    cursor.advance(journal.verify_captured_record(session,1))
    with sqlite3.connect(cursor.path) as con:
        con.execute("UPDATE spot_projection_cursor SET original_sha256=?",
                    ("f"*64,))
    with pytest.raises(ProjectionCursorError,match="cursor moved"):
        cursor.read_verified()


def test_cursor_requires_atomic_source_intent_and_separate_ledger(tmp_path):
    journal,cursor,session,now=_new(tmp_path)
    journal.record_ws(
        session,frame(trade(100,now)),
        stream_kind="aggTrade",received_at_ms=now,
    )
    with sqlite3.connect(journal.path) as con:
        con.execute("UPDATE offline_forward_capture_intents SET sha256=?",
                    ("f"*64,))
    with pytest.raises(ProjectionCursorError,match="atomic source intent"):
        cursor.advance(journal.verify_captured_record(session,1))
    assert cursor.read_verified().last_capture_id==0
    assert cursor.path!=journal.path
