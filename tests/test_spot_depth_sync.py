"""Offline Binance Spot local book: snapshot/diff protocol and failure tests."""
from __future__ import annotations

import pytest

from core.mode import QuantMode
from infrastructure.spot_depth_sync import (
    DepthSyncError, OfflineSpotDepthSynchronizer,
)

BASE = 1_780_000_000_000


def make(*, state=None, max_buffer=16, max_levels=10):
    mode = [QuantMode.SIMULATION] if state is None else state
    return OfflineSpotDepthSynchronizer(
        symbol="BTCUSDT", source="binance_public_simulation",
        session_id="session_001", mode_provider=lambda: mode[0],
        max_buffer=max_buffer, max_levels=max_levels,
    ), mode


def diff(U=100, u=102, *, bid=None, ask=None, event_ms=BASE, symbol="BTCUSDT"):
    return {
        "e": "depthUpdate", "E": event_ms, "s": symbol,
        "U": U, "u": u,
        "b": [] if bid is None else bid,
        "a": [] if ask is None else ask,
    }


def snapshot(last=100, *, bid=None, ask=None):
    return {
        "lastUpdateId": last,
        "bids": [["100.00", "2"], ["99.00", "3"]] if bid is None else bid,
        "asks": [["102.00", "2"], ["103.00", "4"]] if ask is None else ask,
    }


def initial(book, *, event=None, snap=None, receipt=BASE + 101):
    book.ingest_diff(
        diff() if event is None else event, received_at_ms=BASE + 100,
    )
    return book.load_snapshot(
        snapshot() if snap is None else snap,
        received_at_ms=receipt, session_id="session_001",
    )


def test_snapshot_bridge_and_absolute_price_level_updates():
    book, _ = make()
    e = diff(100, 102, bid=[["100.00", "5"], ["99.00", "0"]],
             ask=[["102.00", "0"], ["101.00", "2"]])
    result = initial(book, event=e)
    assert result.status == "SYNCED"
    view = book.view()
    assert view.last_update_id == 102
    assert view.bids == (("100.00", "5"),)
    assert view.asks[0] == ("101.00", "2")
    assert view.snapshot_received_at_ms == BASE + 101
    assert view.available_at_ms == BASE + 101
    assert view.session_id == "session_001"


def test_contiguous_update_and_overlapping_intervals():
    book, _ = make()
    initial(book)
    # Span may overlap previously applied range; Spot uses U<=local+1<=u.
    r = book.ingest_diff(
        diff(102, 106, bid=[["98", "2"]]),
        received_at_ms=BASE + 200,
    )
    assert r.status == "APPLIED" and r.last_update_id == 106
    view = book.view(as_of_ms=BASE + 200)
    assert view.last_update_id == 106
    assert view.available_at_ms == BASE + 200
    with pytest.raises(DepthSyncError, match="as-of"):
        book.view(as_of_ms=BASE + 199)


def test_pre_snapshot_old_event_is_dropped_and_later_bridge_applied():
    book, _ = make()
    book.ingest_diff(diff(100, 101), received_at_ms=BASE + 100)
    book.ingest_diff(diff(102, 104), received_at_ms=BASE + 110)
    result = book.load_snapshot(
        snapshot(103), received_at_ms=BASE + 120,
        session_id="session_001",
    )
    assert result.status == "SYNCED"
    assert book.view().last_update_id == 104


def test_snapshot_older_than_first_buffered_U_requires_refetch():
    book, _ = make()
    book.ingest_diff(diff(110, 112), received_at_ms=BASE + 100)
    result = book.load_snapshot(
        snapshot(109), received_at_ms=BASE + 110,
        session_id="session_001",
    )
    assert result.status == "AWAITING_NEW_SNAPSHOT"
    with pytest.raises(DepthSyncError):
        book.view()
    again = book.load_snapshot(
        snapshot(110), received_at_ms=BASE + 120,
        session_id="session_001",
    )
    assert again.status == "SYNCED"
    assert book.view().last_update_id == 112


def test_snapshot_ahead_of_buffer_waits_for_next_bridge_without_exposing_book():
    book, _ = make()
    book.ingest_diff(diff(100, 101), received_at_ms=BASE + 100)
    res = book.load_snapshot(
        snapshot(200), received_at_ms=BASE + 110,
        session_id="session_001",
    )
    assert res.status == "WAITING_BRIDGE"
    with pytest.raises(DepthSyncError, match="unavailable"):
        book.view()
    assert book.ingest_diff(
        diff(197, 200), received_at_ms=BASE + 120,
    ).status == "IGNORED"
    assert book.ingest_diff(
        diff(200, 205), received_at_ms=BASE + 130,
    ).status == "APPLIED"
    assert book.view().last_update_id == 205


def test_gap_requires_explicit_reconnect_and_new_snapshot():
    book, _ = make()
    initial(book)
    assert book.ingest_diff(
        diff(105, 110), received_at_ms=BASE + 200,
    ).status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError):
        book.view()
    assert book.ingest_diff(diff(), received_at_ms=BASE + 210).status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError, match="distinct"):
        book.reconnect("session_001")
    book.reconnect("session_002")
    assert book.status == "BUFFERING"
    with pytest.raises(DepthSyncError, match="requires pending"):
        book.load_snapshot(
            snapshot(), received_at_ms=BASE + 300, session_id="session_002",
        )
    book.ingest_diff(diff(220, 221, event_ms=BASE + 310), received_at_ms=BASE + 310)
    book.load_snapshot(
        snapshot(220), received_at_ms=BASE + 320, session_id="session_002",
    )
    assert book.view().session_id == "session_002"


def test_buffer_overflow_is_fail_closed():
    book, _ = make(max_buffer=1)
    assert book.ingest_diff(diff(), received_at_ms=BASE + 100).status == "BUFFERED"
    assert book.ingest_diff(
        diff(103, 104), received_at_ms=BASE + 110,
    ).status == "RESET_REQUIRED"
    assert book.status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError):
        book.view()


def test_identical_buffered_duplicate_and_conflicting_duplicate():
    book, _ = make()
    e = diff()
    assert book.ingest_diff(e, received_at_ms=BASE + 100).status == "BUFFERED"
    assert book.ingest_diff(e, received_at_ms=BASE + 100).status == "IGNORED"
    altered = diff(bid=[["100.00", "8"]])
    assert book.ingest_diff(
        altered, received_at_ms=BASE + 120,
    ).status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError):
        book.view()


def test_out_of_order_buffer_resets_and_duplicate_applied_ignored():
    book, _ = make()
    book.ingest_diff(diff(100, 102), received_at_ms=BASE + 100)
    assert book.ingest_diff(
        diff(99, 100), received_at_ms=BASE + 120,
    ).status == "RESET_REQUIRED"
    book.reconnect("session_002")
    book.ingest_diff(diff(), received_at_ms=BASE + 100)
    book.load_snapshot(snapshot(), received_at_ms=BASE + 101, session_id="session_002")
    assert book.ingest_diff(diff(), received_at_ms=BASE + 200).status == "IGNORED"


def test_crossed_snapshot_and_crossed_update_fail_closed():
    book, _ = make()
    book.ingest_diff(diff(), received_at_ms=BASE + 100)
    with pytest.raises(DepthSyncError, match="crossed"):
        book.load_snapshot(
            snapshot(100, bid=[["105", "1"]]),
            received_at_ms=BASE + 101, session_id="session_001",
        )
    # Invalid snapshot did not install a partial book and a valid one can follow.
    assert book.status == "BUFFERING"
    book.load_snapshot(snapshot(), received_at_ms=BASE + 102,
                       session_id="session_001")
    assert book.ingest_diff(
        diff(103, 103, bid=[["106", "2"]]), received_at_ms=BASE + 200,
    ).status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError):
        book.view()


@pytest.mark.parametrize("bad", [
    {"lastUpdateId": 101, "bids": [], "asks": []},
    {"lastUpdateId": 101, "bids": [["100", "-1"]], "asks": [["102", "1"]]},
    {"lastUpdateId": 101, "bids": [["100", "1"], ["100", "2"]],
     "asks": [["102", "1"]]},
    {"lastUpdateId": 101, "bids": [["NaN", "1"]], "asks": [["102", "1"]]},
    {"lastUpdateId": 101, "bids": [["100", "1"]], "asks": [["102", "inf"]]},
    {"lastUpdateId": 101, "bids": [["100", "1"]], "asks": [["102", "1"]],
     "U": 99},
])
def test_bad_snapshot_rejected_without_state_mutation(bad):
    book, _ = make()
    book.ingest_diff(diff(), received_at_ms=BASE + 100)
    with pytest.raises(DepthSyncError):
        book.load_snapshot(bad, received_at_ms=BASE + 105, session_id="session_001")
    assert book.status == "BUFFERING"


@pytest.mark.parametrize("mutator", [
    lambda d: d.update(s="ETHUSDT"),
    lambda d: d.update(pu=99),
    lambda d: d.update(lastUpdateId=100),
    lambda d: d.update(U=105, u=100),
    lambda d: d.update(U=True),
    lambda d: d.update(E=BASE + 5000),
    lambda d: d.update(E=BASE - 60_000),
    lambda d: d.update(b=[["100", "-1"]]),
    lambda d: d.update(a=[["101", "NaN"]]),
    lambda d: d.update(b=[["100", "1"], ["100", "3"]]),
    lambda d: d.update(b=[["100", "2", "EXTRA"]]),
])
def test_bad_spot_diff_rejected(mutator):
    book, _ = make()
    payload = diff()
    mutator(payload)
    with pytest.raises(DepthSyncError):
        book.ingest_diff(payload, received_at_ms=BASE + 100)
    # A rejected websocket packet might contain indispensable updates:
    # do not silently continue buffering or publish an old book.
    assert book.status == "RESET_REQUIRED"


def test_wrong_session_and_early_snapshot_receipt_rejected():
    book, _ = make()
    book.ingest_diff(diff(), received_at_ms=BASE + 100)
    with pytest.raises(DepthSyncError, match="another socket session"):
        book.load_snapshot(snapshot(), received_at_ms=BASE + 110,
                           session_id="wrong")
    with pytest.raises(DepthSyncError, match="follow"):
        book.load_snapshot(snapshot(), received_at_ms=BASE + 99,
                           session_id="session_001")
    assert book.status == "BUFFERING"


def test_simulation_only_and_configuration_fail_closed():
    mode = [QuantMode.SIMULATION]
    book, _ = make(state=mode)
    book.ingest_diff(diff(), received_at_ms=BASE + 100)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        book.ingest_diff(diff(), received_at_ms=BASE + 110)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        book.load_snapshot(snapshot(), received_at_ms=BASE + 120,
                           session_id="session_001")
    with pytest.raises(RuntimeError, match="SIMULATION"):
        book.view()
    with pytest.raises(RuntimeError, match="SIMULATION"):
        book.reconnect("session_002")
    mode[0] = QuantMode.SIMULATION
    assert book.load_snapshot(snapshot(), received_at_ms=BASE + 130,
                              session_id="session_001").status == "SYNCED"
    with pytest.raises(DepthSyncError):
        OfflineSpotDepthSynchronizer(
            symbol="BTCUSDT", source="r", session_id="a",
            market="binance_usdm", mode_provider=lambda: mode[0],
        )
    with pytest.raises(DepthSyncError):
        make(max_levels=5001)


def test_book_growth_past_configured_max_levels_requires_resync():
    book, _ = make(max_levels=2)
    initial(book)
    result = book.ingest_diff(
        diff(103, 103, bid=[["98", "1"]]), received_at_ms=BASE + 200,
    )
    assert result.status == "RESET_REQUIRED"
    assert book.status == "RESET_REQUIRED"


def test_clock_regression_resets_instead_of_backdating_book():
    book, _ = make()
    initial(book)
    result = book.ingest_diff(
        diff(103, 104), received_at_ms=BASE + 100,
    )
    assert result.status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError):
        book.view()


def test_malformed_packet_after_sync_invalidates_book():
    book, _ = make()
    initial(book)
    with pytest.raises(DepthSyncError, match="wrong stream"):
        book.ingest_diff(
            diff(103, 104, symbol="ETHUSDT"),
            received_at_ms=BASE + 200,
        )
    assert book.status == "RESET_REQUIRED"
    with pytest.raises(DepthSyncError):
        book.view()


def test_non_json_payload_is_rejected_as_depth_sync_error():
    book, _ = make()
    event = diff()
    event["unknown_extra"] = {1, 2}
    with pytest.raises(DepthSyncError, match="non-JSON"):
        book.ingest_diff(event, received_at_ms=BASE + 100)
    assert book.status == "RESET_REQUIRED"
