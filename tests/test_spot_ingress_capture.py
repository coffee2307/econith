"""Immutable offline Spot transport capture and point-in-time replay checks."""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.mode import QuantMode
from infrastructure.spot_capture_replay import OfflineSpotCaptureReplayer
from infrastructure.spot_ingress_capture import (
    OfflineSpotCaptureJournal, SpotCaptureError,
)

BASE = 1_780_000_000_000
WS = "wss://stream.binance.com:9443/stream"
REST = "https://api.binance.com/api/v3/depth"


def trade(n, *, price="100.00", symbol="BTCUSDT"):
    return {
        "e": "aggTrade", "E": BASE, "T": BASE - 10, "s": symbol,
        "a": n, "f": n, "l": n, "p": price, "q": "0.03",
        "m": False,
    }


def wrapped(data, *, stream=None):
    if stream is None:
        stream = "btcusdt@aggTrade" if data["e"] == "aggTrade" else "btcusdt@depth@100ms"
    return json.dumps({"stream": stream, "data": data}, separators=(",", ":"))


def diff(U=100, u=102, *, bids=None, asks=None):
    return {
        "e": "depthUpdate", "E": BASE, "s": "BTCUSDT",
        "U": U, "u": u,
        "b": [] if bids is None else bids,
        "a": [] if asks is None else asks,
    }


def snapshot(n=100):
    return json.dumps({
        "lastUpdateId": n, "bids": [["100.00", "2"], ["99.0", "1"]],
        "asks": [["102.0", "3"], ["103.0", "2"]],
    })


def ready(tmp_path, *, max_frames=100_000):
    mode = [QuantMode.SIMULATION]
    journal = OfflineSpotCaptureJournal(
        tmp_path / "ingress.db", mode_provider=lambda: mode[0],
        max_frames_per_session=max_frames,
    )
    journal.start_session(
        "ws1", symbol="BTCUSDT", source="binance_public_simulation",
        ws_endpoint=WS, opened_at_ms=BASE,
    )
    return journal, mode


def ws(journal, event, when, *, kind=None):
    return journal.record_ws(
        "ws1", wrapped(event),
        stream_kind=kind or event["e"], received_at_ms=when,
    )


def rest(journal, when, *, body=None, req=None, symbol="BTCUSDT", endpoint=REST):
    return journal.record_snapshot(
        "ws1", snapshot() if body is None else body, rest_endpoint=endpoint,
        request_symbol=symbol,
        request_started_at_ms=BASE + 50 if req is None else req,
        received_at_ms=when,
    )


def test_exact_raw_bytes_preserved_and_duplicate_transport_recorded(tmp_path):
    j, _ = ready(tmp_path)
    raw = ('{  "stream":"btcusdt@aggTrade", "data":' +
           json.dumps(trade(100), separators=(",", ":")) + " }").encode()
    first = j.record_ws("ws1", raw, stream_kind="aggTrade", received_at_ms=BASE + 100)
    second = j.record_ws("ws1", raw, stream_kind="aggTrade", received_at_ms=BASE + 200)
    assert second > first
    captured = j.replay_as_of("ws1", BASE + 300)
    assert [r.capture_id for r in captured] == [first, second]
    assert captured[0].raw == raw and captured[1].raw == raw
    assert captured[0].received_at_ms == BASE + 100
    assert captured[1].received_at_ms == BASE + 200


def test_snapshot_combined_diff_trades_replay_asof_and_reopen(tmp_path):
    j, mode = ready(tmp_path)
    ws(j, trade(100), BASE + 100)
    ws(j, diff(), BASE + 110)
    rest(j, BASE + 200)
    ws(j, trade(101, price="101.00"), BASE + 210)
    offline = OfflineSpotCaptureReplayer(j)
    just_trade = offline.replay("ws1", as_of_ms=BASE + 109)
    assert just_trade.book is None
    assert just_trade.last_agg_trade_id == 100
    buffered = offline.replay("ws1", as_of_ms=BASE + 199)
    assert buffered.depth_status == "BUFFERING" and buffered.book is None
    synced = offline.replay("ws1", as_of_ms=BASE + 200)
    assert synced.book.last_update_id == 102
    assert synced.book.available_at_ms == BASE + 200
    assert len(synced.trades) == 1
    later = offline.replay("ws1", as_of_ms=BASE + 210)
    assert later.last_agg_trade_id == 101
    assert len(later.trades) == 2
    reopened = OfflineSpotCaptureJournal(
        j.path, mode_provider=lambda: mode[0],
    )
    assert OfflineSpotCaptureReplayer(reopened).replay(
        "ws1", as_of_ms=BASE + 210,
    ) == later


def test_capture_stores_diff_original_not_partial_top20_or_simulated_overlay(tmp_path):
    j, _ = ready(tmp_path)
    for bad in (
        {"stream": "btcusdt@depth20@100ms", "data": {
            "lastUpdateId": 101, "bids": [], "asks": [],
        }},
        {"stream": "btcusdt@aggTrade", "data": {
            **trade(100), "s": "ETHUSDT",
        }},
        {"stream": "ethusdt@aggTrade", "data": trade(100)},
        {"stream": "btcusdt@depth@100ms", "data": {
            **diff(), "pu": 99,
        }},
    ):
        body = json.dumps(bad)
        kind = "aggTrade" if "aggTrade" in bad["stream"] else "depthUpdate"
        with pytest.raises(SpotCaptureError):
            j.record_ws("ws1", body, stream_kind=kind, received_at_ms=BASE + 100)
    assert j.replay_as_of("ws1", BASE + 300) == []


def test_wrong_rest_symbol_route_request_clock_and_wrong_snapshot_type(tmp_path):
    j, _ = ready(tmp_path)
    for changes in (
        {"symbol": "ETHUSDT"},
        {"endpoint": "https://evil.example/api/v3/depth"},
        {"req": BASE - 1},
        {"req": BASE + 300},
        {"body": json.dumps({"e": "depthUpdate", "U": 100, "u": 102})},
    ):
        with pytest.raises(SpotCaptureError):
            rest(j, BASE + 200, **changes)
    assert j.replay_as_of("ws1", BASE + 300) == []


@pytest.mark.parametrize("payload", [
    b"",
    b"not json",
    b'{"stream":"btcusdt@aggTrade","stream":"btcusdt@aggTrade","data":{}}',
    b'{"stream":"btcusdt@aggTrade","data":{"a":NaN}}',
    b'{"stream":123,"data":{}}',
    b'{"stream":"btcusdt@aggTrade","data":[]}',
    b'\xff\xfe',
])
def test_bad_raw_websocket_body_does_not_commit(tmp_path, payload):
    j, _ = ready(tmp_path)
    with pytest.raises(SpotCaptureError):
        j.record_ws("ws1", payload, stream_kind="aggTrade", received_at_ms=BASE + 100)
    assert not j.replay_as_of("ws1", BASE + 300)


def test_capture_fail_closed_on_backdated_receipts_and_capacity(tmp_path):
    j, _ = ready(tmp_path, max_frames=1)
    ws(j, trade(100), BASE + 200)
    with pytest.raises(SpotCaptureError, match="backdated"):
        ws(j, trade(101), BASE + 150)
    with pytest.raises(SpotCaptureError, match="capacity"):
        ws(j, trade(101), BASE + 250)
    assert len(j.replay_as_of("ws1", BASE + 300)) == 1


def test_duplicate_session_id_and_closed_session_rejected(tmp_path):
    j, _ = ready(tmp_path)
    ws(j, trade(100), BASE + 100)
    with pytest.raises(sqlite3.IntegrityError):
        j.start_session(
            "ws1", symbol="BTCUSDT", source="binance_public_simulation",
            ws_endpoint=WS, opened_at_ms=BASE + 101,
        )
    with pytest.raises(SpotCaptureError):
        j.close_session("ws1", closed_at_ms=BASE + 99)
    j.close_session("ws1", closed_at_ms=BASE + 200)
    with pytest.raises(SpotCaptureError, match="closed"):
        ws(j, trade(101), BASE + 300)
    assert len(j.replay_as_of("ws1", BASE + 400)) == 1


def test_reality_blocks_record_session_replay_and_close(tmp_path):
    j, mode = ready(tmp_path)
    ws(j, trade(100), BASE + 100)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        j.record_ws("ws1", wrapped(trade(101)),
                    stream_kind="aggTrade", received_at_ms=BASE + 101)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        j.start_session("new", symbol="BTCUSDT",
                        source="binance_public_simulation",
                        ws_endpoint=WS, opened_at_ms=BASE + 300)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        j.replay_as_of("ws1", BASE + 300)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        j.close_session("ws1", closed_at_ms=BASE + 300)


@pytest.mark.parametrize("column,value", [
    ("raw_blob", b'{"forged":true}'),
    ("sha256", "f" * 64),
    ("kind", "snapshot"),
    ("endpoint", "https://evil.example"),
    ("received_at_ms", BASE - 30),
    ("request_started_at_ms", BASE - 100),
])
def test_tampered_source_bytes_metadata_or_clock_fail_replay(tmp_path, column, value):
    j, _ = ready(tmp_path)
    ws(j, trade(100), BASE + 100)
    with sqlite3.connect(j.path) as conn:
        conn.execute(f"UPDATE offline_spot_capture_records SET {column}=?", (value,))
    with pytest.raises(SpotCaptureError):
        j.replay_as_of("ws1", BASE + 300)


def test_replay_detects_gap_and_conflicting_duplicate_agg_trades(tmp_path):
    j, _ = ready(tmp_path)
    ws(j, trade(100), BASE + 100)
    ws(j, trade(102), BASE + 200)
    with pytest.raises(SpotCaptureError, match="sequence gap"):
        OfflineSpotCaptureReplayer(j).replay("ws1", as_of_ms=BASE + 300)
    # New session can have its own baseline; never assume unseen prior history.
    j.start_session("ws2", symbol="BTCUSDT", source="binance_public_simulation",
                    ws_endpoint=WS, opened_at_ms=BASE)
    j.record_ws("ws2", wrapped(trade(100)),
                stream_kind="aggTrade", received_at_ms=BASE + 100)
    j.record_ws("ws2", wrapped(trade(100, price="99")),
                stream_kind="aggTrade", received_at_ms=BASE + 200)
    with pytest.raises(SpotCaptureError, match="conflicting duplicate"):
        OfflineSpotCaptureReplayer(j).replay("ws2", as_of_ms=BASE + 300)


def test_replay_refuses_book_after_depth_gap(tmp_path):
    j, _ = ready(tmp_path)
    ws(j, diff(), BASE + 100)
    rest(j, BASE + 200)
    ws(j, diff(107, 110), BASE + 300)
    with pytest.raises(SpotCaptureError, match="depth replay lost continuity"):
        OfflineSpotCaptureReplayer(j).replay("ws1", as_of_ms=BASE + 400)
    assert OfflineSpotCaptureReplayer(j).replay(
        "ws1", as_of_ms=BASE + 200,
    ).book.last_update_id == 102


def test_snapshot_wrong_order_or_bad_book_not_reported_as_synced(tmp_path):
    j, _ = ready(tmp_path)
    rest(j, BASE + 100, req=BASE + 50)
    ws(j, diff(), BASE + 200)
    with pytest.raises(ValueError, match="snapshot requires pending"):
        OfflineSpotCaptureReplayer(j).replay("ws1", as_of_ms=BASE + 300)


def test_replay_asof_fails_closed_if_limit_would_truncate(tmp_path):
    j, _ = ready(tmp_path)
    for n in range(100, 104):
        ws(j, trade(n), BASE + n)
    with pytest.raises(SpotCaptureError, match="replay limit exceeded"):
        j.replay_as_of("ws1", BASE + 300, limit=2)


def test_concurrent_ingest_has_monotonic_db_order_without_loss(tmp_path):
    j, _ = ready(tmp_path)
    def send(n):
        return ws(j, trade(100 + n), BASE + 100)
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(send, range(20)))
    assert len(set(ids)) == 20
    events = j.replay_as_of("ws1", BASE + 200)
    assert len(events) == 20
    assert [e.capture_id for e in events] == sorted(ids)


def test_insert_failure_rolls_back_clock_and_count(tmp_path):
    j, _ = ready(tmp_path)
    with sqlite3.connect(j.path) as conn:
        conn.execute("""
            CREATE TRIGGER stop_capture BEFORE INSERT ON offline_spot_capture_records
            BEGIN SELECT RAISE(ABORT, 'injected disk error'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="injected disk error"):
        ws(j, trade(100), BASE + 200)
    with sqlite3.connect(j.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_spot_capture_records"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT last_receipt_ms FROM offline_spot_capture_sessions"
        ).fetchone()[0] == BASE
        conn.execute("DROP TRIGGER stop_capture")
    ws(j, trade(100), BASE + 100)
    assert len(j.replay_as_of("ws1", BASE + 300)) == 1


def test_sessions_are_isolated_and_no_cross_session_snapshot(tmp_path):
    j, _ = ready(tmp_path)
    j.start_session("ws2", symbol="ETHUSDT", source="binance_public_simulation",
                    ws_endpoint=WS, opened_at_ms=BASE)
    ws(j, trade(100), BASE + 100)
    j.record_ws("ws2", wrapped(trade(500, symbol="ETHUSDT"),
                        stream="ethusdt@aggTrade"),
                stream_kind="aggTrade", received_at_ms=BASE + 150)
    with pytest.raises(SpotCaptureError):
        j.record_snapshot(
            "ws2", snapshot(), rest_endpoint=REST,
            request_symbol="BTCUSDT", request_started_at_ms=BASE,
            received_at_ms=BASE + 200,
        )
    assert len(j.replay_as_of("ws1", BASE + 300)) == 1
    assert len(j.replay_as_of("ws2", BASE + 300)) == 1
