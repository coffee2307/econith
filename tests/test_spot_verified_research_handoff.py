"""Recorded original WS -> as-of journal -> projection -> independent receiver."""
from __future__ import annotations

import json
import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.market_data_integrity import OfflineMarketIntegrityJournal
from infrastructure.market_delivery_cursor import OfflineMarketProjectionCursor
from infrastructure.market_projection_inbox import OfflineMarketProjectionInbox
from infrastructure.market_cross_process_delivery import (
    OfflineCrossProcessProjectionSender, OfflineIndependentProjectionReceiver,
    CrossProcessDeliveryError,
)
from infrastructure.spot_ingress_capture import (
    OfflineSpotCaptureJournal, SpotCaptureError,
)
from infrastructure.spot_verified_research_handoff import (
    OfflineSpotVerifiedResearchHandoff,
)

BASE = 1_780_000_000_000
WS = "wss://data-stream.binance.vision/stream"
REST = "https://data-api.binance.vision/api/v3/depth"


def ws_frame(e):
    return json.dumps({
        "stream": "btcusdt@aggTrade" if e["e"] == "aggTrade"
                  else "btcusdt@depth@100ms",
        "data": e,
    }, separators=(",", ":")).encode()


def trade(n):
    return {
        "e": "aggTrade", "E": BASE, "T": BASE-10,
        "s": "BTCUSDT", "a": n, "f": n, "l": n,
        "p": str(n), "q": "0.01", "m": False,
    }


def depth():
    return {
        "e": "depthUpdate", "E": BASE, "s": "BTCUSDT",
        "U": 100, "u": 102,
        "b": [["100","3"]], "a": [],
    }


def make(tmp_path, *, trade_ids=(500,501), closed=True, include_depth=True):
    mode = [QuantMode.SIMULATION]
    raw = OfflineSpotCaptureJournal(
        tmp_path / "raw.sqlite", mode_provider=lambda: mode[0],
    )
    raw.start_session(
        "spot_session", symbol="BTCUSDT",
        source="binance_public_simulation",
        ws_endpoint=WS, opened_at_ms=BASE+50,
    )
    raw.record_ws("spot_session", ws_frame(trade(trade_ids[0])),
                  stream_kind="aggTrade", received_at_ms=BASE+100)
    if include_depth:
        raw.record_ws("spot_session", ws_frame(depth()),
                      stream_kind="depthUpdate", received_at_ms=BASE+110)
        raw.record_snapshot(
            "spot_session", json.dumps({
                "lastUpdateId": 100,
                "bids": [["100","2"], ["99","1"]],
                "asks": [["102","3"], ["103","1"]],
            }).encode(),
            rest_endpoint=REST, request_symbol="BTCUSDT",
            request_started_at_ms=BASE+120, received_at_ms=BASE+140,
        )
    for i, n in enumerate(trade_ids[1:]):
        raw.record_ws("spot_session", ws_frame(trade(n)),
                      stream_kind="aggTrade",
                      received_at_ms=BASE+150+i)
    if closed:
        raw.close_session("spot_session", closed_at_ms=BASE+200)
    journal = OfflineMarketIntegrityJournal(
        tmp_path / "verified.sqlite", market="binance_spot",
        mode_provider=lambda: mode[0],
    )
    bridge = OfflineSpotVerifiedResearchHandoff(raw, journal)
    return bridge, raw, journal, mode


def test_full_three_database_verified_delivery_crash_recovery(tmp_path):
    handoff, raw, integrity, flag = make(tmp_path)
    promoted = handoff.promote_session(
        "spot_session", as_of_ms=BASE+300,
    )
    assert promoted.admitted == 2 and promoted.duplicates == 0
    assert promoted.book_last_update_id == 102
    assert [x.available_at_ms for x in integrity.replay_as_of(BASE+300)] == [
        BASE+100, BASE+150,
    ]
    assert [x.capture_id for x in handoff.manifest("spot_session")] == [
        x.capture_id for x in promoted.records
    ]
    producer = OfflineMarketProjectionCursor(integrity)
    for i in range(2):
        ticket = producer.claim_next("indicator", now_ms=BASE+350+i)
        assert ticket is not None
        producer.commit_projection(
            ticket, {"price":ticket.event.frame["p"]},
            now_ms=BASE+360+i,
        )
    inbox = OfflineMarketProjectionInbox(producer)
    sender = OfflineCrossProcessProjectionSender(inbox)
    receiver = OfflineIndependentProjectionReceiver(
        tmp_path / "receiver.sqlite",
        mode_provider=lambda: flag[0],
    )
    assert len({
        str(raw.path.resolve()), str(integrity.path.resolve()),
        str(receiver.path.resolve()),
    }) == 3
    with pytest.raises(CrossProcessDeliveryError, match="injected sender crash"):
        sender.deliver_once(
            "indicator", "risk",
            receiver_accept=lambda env: receiver.accept(env, now_ms=BASE+500),
            now_ms=BASE+500, lease_ms=10,
            fail_after_receiver_commit=True,
        )
    assert len(receiver.receipts("indicator", "risk")) == 1
    assert inbox.receipts("indicator", "risk") == []
    for t in (BASE+511, BASE+512):
        assert sender.deliver_once(
            "indicator", "risk",
            receiver_accept=lambda env: receiver.accept(env, now_ms=t),
            now_ms=t,
        )
    assert len(receiver.receipts("indicator", "risk")) == 2
    assert len(inbox.receipts("indicator", "risk")) == 2
    assert sender.deliver_once(
        "indicator", "risk",
        receiver_accept=lambda env: receiver.accept(env, now_ms=BASE+520),
        now_ms=BASE+520,
    ) is None


def test_asof_before_book_bridge_refuses_promotion_without_mutation(tmp_path):
    handoff, raw, journal, _ = make(tmp_path)
    with pytest.raises(SpotCaptureError, match="cannot precede"):
        handoff.promote_session("spot_session", as_of_ms=BASE+139)
    assert journal.replay_as_of(BASE+300) == []


def test_open_session_and_book_without_bridge_not_admitted(tmp_path):
    handoff, raw, journal, _ = make(tmp_path, closed=False)
    with pytest.raises(SpotCaptureError, match="closed"):
        handoff.promote_session("spot_session", as_of_ms=BASE+300)
    raw.close_session("spot_session", closed_at_ms=BASE+200)
    no_book, _, no_book_journal, _ = make(tmp_path / "separate", include_depth=False)
    with pytest.raises(SpotCaptureError, match="unsynchronized"):
        no_book.promote_session("spot_session", as_of_ms=BASE+300)
    assert no_book_journal.replay_as_of(BASE+300) == []


def test_gap in_original_aggtrade_fails_before_market_admission(tmp_path):
    handoff, _, journal, _ = make(tmp_path, trade_ids=(500,503))
    with pytest.raises(SpotCaptureError, match="sequence gap"):
        handoff.promote_session("spot_session", as_of_ms=BASE+300)
    assert journal.replay_as_of(BASE+300) == []


def test_repeated_promotion_deduplicates_source_and_keeps_first_receipts(tmp_path):
    handoff, _, journal, _ = make(tmp_path)
    first = handoff.promote_session("spot_session", as_of_ms=BASE+300)
    second = handoff.promote_session("spot_session", as_of_ms=BASE+400)
    assert first.admitted == 2 and second.admitted == 0
    assert second.duplicates == 2
    assert len(handoff.manifest("spot_session")) == 2
    assert [e.available_at_ms for e in journal.replay_as_of(BASE+500)] == [
        BASE+100, BASE+150,
    ]


def test_crash_after_source_journal_commit_before_manifest_can_restart(tmp_path):
    handoff, _, journal, _ = make(tmp_path, trade_ids=(500,))
    with sqlite3.connect(journal.path) as db:
        db.execute("""
            CREATE TRIGGER deny_manifest BEFORE INSERT ON
                offline_spot_promotion_manifest
            BEGIN SELECT RAISE(ABORT,'promotion checkpoint crashed'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="promotion checkpoint crashed"):
        handoff.promote_session("spot_session", as_of_ms=BASE+300)
    assert len(journal.replay_as_of(BASE+300)) == 1
    assert handoff.manifest("spot_session") == []
    with sqlite3.connect(journal.path) as db:
        db.execute("DROP TRIGGER deny_manifest")
    retry = handoff.promote_session("spot_session", as_of_ms=BASE+300)
    assert retry.admitted == 0 and retry.duplicates == 1
    assert len(handoff.manifest("spot_session")) == 1


def test_manifest_tampering_invalidates_lineage(tmp_path):
    handoff, _, journal, _ = make(tmp_path, trade_ids=(500,))
    handoff.promote_session("spot_session", as_of_ms=BASE+300)
    with sqlite3.connect(journal.path) as db:
        db.execute("""
            UPDATE offline_spot_promotion_manifest
            SET raw_sha256='0'
        """)
    with pytest.raises(SpotCaptureError, match="manifest lost"):
        handoff.manifest("spot_session")


def test_scope_and_mode_failure_do_not_emit_market_events(tmp_path):
    handoff, raw, journal, mode = make(tmp_path)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        handoff.promote_session("spot_session", as_of_ms=BASE+300)
