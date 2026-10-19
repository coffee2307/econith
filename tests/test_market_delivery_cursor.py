"""Phase 1 durable per-consumer projection cursor and lease fencing."""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.mode import QuantMode
from infrastructure.market_data_integrity import (
    MarketEventError, OfflineMarketIntegrityJournal,
)
from infrastructure.market_delivery_cursor import (
    DeliveryConflict, OfflineMarketProjectionCursor,
)

BASE = 1_780_000_000_000


def trade(n, *, timestamp=BASE):
    return {
        "e": "aggTrade", "s": "BTCUSDT", "a": n,
        "E": timestamp, "T": timestamp - 10,
        "f": n, "l": n, "p": str(n), "q": "0.01", "m": False,
    }


def ready(tmp_path, *, mode=None, receipts=(BASE + 100, BASE + 200)):
    state = [QuantMode.SIMULATION] if mode is None else mode
    journal = OfflineMarketIntegrityJournal(
        tmp_path / "market.db",
        mode_provider=lambda: state[0],
        market="binance_spot",
    )
    for i, when in enumerate(receipts):
        result = journal.ingest_agg_trade(
            trade(100 + i), source="research",
            received_at_ms=when,
        )
        assert result.accepted
    return OfflineMarketProjectionCursor(journal), journal, state


def claim(cursor, consumer="indicator", *, now=BASE + 300, **kwargs):
    return cursor.claim_next(
        consumer, now_ms=now, **kwargs,
    )


def commit(cursor, ticket, value=None, *, now=BASE + 301):
    if value is None:
        value = {"mid": ticket.event.frame["p"]}
    return cursor.commit_projection(ticket, value, now_ms=now)


def test_atomic_projection_cursor_sequencing_and_replay(tmp_path):
    cursor, journal, _ = ready(tmp_path)
    first = claim(cursor)
    assert first and first.event.sequence == 100
    assert cursor.cursor_position("indicator") == 0
    assert cursor.projections("indicator") == []
    assert claim(cursor) is None
    persisted = commit(cursor, first)
    assert persisted.value == {"mid": "100"}
    assert cursor.cursor_position("indicator") == first.ingest_id
    second = claim(cursor)
    assert second and second.event.sequence == 101
    assert commit(cursor, second).value == {"mid": "101"}
    assert claim(cursor) is None
    assert [p.event_id for p in cursor.projections("indicator")] == [
        f"md:binance_spot:research:BTCUSDT:aggTrade:{n}" for n in (100, 101)
    ]
    assert len(journal.replay_as_of(BASE + 300)) == 2


def test_expired_claim_reissued_with_new_token_stale_worker_fenced(tmp_path):
    cursor, _, _ = ready(tmp_path)
    original = claim(cursor, lease_ms=20)
    assert claim(cursor, now=BASE + 310) is None
    replacement = claim(cursor, now=BASE + 321)
    assert replacement is not None
    assert replacement.event.event_id == original.event.event_id
    assert replacement.token != original.token
    with pytest.raises(DeliveryConflict, match="superseded"):
        commit(cursor, original, now=BASE + 322)
    assert cursor.cursor_position("indicator") == 0
    commit(cursor, replacement, now=BASE + 322)
    assert cursor.cursor_position("indicator") == original.ingest_id


def test_expired_claim_cannot_be_committed_without_replacement(tmp_path):
    cursor, _, _ = ready(tmp_path)
    original = claim(cursor, lease_ms=10)
    with pytest.raises(DeliveryConflict, match="expired"):
        commit(cursor, original, now=BASE + 310)
    assert cursor.cursor_position("indicator") == 0


def test_callback_failure_before_commit_leaves_source_pending_after_restart(tmp_path):
    cursor, journal, mode = ready(tmp_path)
    previous = claim(cursor, lease_ms=25)
    try:
        raise ValueError("projection callback crashed")
    except ValueError:
        pass
    restarted_journal = OfflineMarketIntegrityJournal(
        journal.path, market="binance_spot", mode_provider=lambda: mode[0],
    )
    restarted = OfflineMarketProjectionCursor(restarted_journal)
    assert restarted.cursor_position("indicator") == 0
    next_ticket = claim(restarted, now=BASE + 326)
    assert next_ticket.event.event_id == previous.event.event_id
    commit(restarted, next_ticket, now=BASE + 327)
    assert restarted.cursor_position("indicator") == previous.ingest_id


def test_concurrent_claim_one_winner_per_consumer(tmp_path):
    cursor, _, _ = ready(tmp_path)
    with ThreadPoolExecutor(max_workers=12) as pool:
        attempts = list(pool.map(lambda _: claim(cursor), range(32)))
    accepted = [c for c in attempts if c is not None]
    assert len(accepted) == 1
    commit(cursor, accepted[0])
    assert len(cursor.projections("indicator")) == 1


def test_independent_consumers_have_independent_progress(tmp_path):
    cursor, _, _ = ready(tmp_path)
    first = claim(cursor, "momentum")
    other = claim(cursor, "risk")
    assert first and other and first.event.event_id == other.event.event_id
    commit(cursor, first)
    assert cursor.cursor_position("momentum") == first.ingest_id
    assert cursor.cursor_position("risk") == 0
    commit(cursor, other)
    assert len(cursor.projections("momentum")) == 1
    assert len(cursor.projections("risk")) == 1


def test_projection_payload_validation_does_not_advance_cursor(tmp_path):
    cursor, _, _ = ready(tmp_path)
    ticket = claim(cursor)
    with pytest.raises(ValueError, match="finite JSON"):
        commit(cursor, ticket, {"bad": float("nan")})
    with pytest.raises(ValueError, match="mapping"):
        commit(cursor, ticket, [1, 2])
    assert cursor.cursor_position("indicator") == 0
    commit(cursor, ticket)
    assert len(cursor.projections("indicator")) == 1


def test_atomic_commit_failure_rolls_back_projection_and_cursor(tmp_path):
    cursor, journal, _ = ready(tmp_path)
    ticket = claim(cursor)
    with sqlite3.connect(journal.path) as conn:
        conn.execute("""
            CREATE TRIGGER reject_position BEFORE INSERT
            ON offline_market_delivery_cursors
            BEGIN SELECT RAISE(ABORT, 'injected cursor failure'); END
        """)
    with pytest.raises(sqlite3.DatabaseError, match="cursor failure"):
        commit(cursor, ticket)
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_market_projections"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_market_delivery_cursors"
        ).fetchone()[0] == 0
        conn.execute("DROP TRIGGER reject_position")
    commit(cursor, ticket)
    assert len(cursor.projections("indicator")) == 1


def test_projection_durable_after_crash_without_any_bus_ack(tmp_path):
    cursor, journal, mode = ready(tmp_path)
    ticket = claim(cursor)
    expected = commit(cursor, ticket)
    reopened = OfflineMarketIntegrityJournal(
        journal.path, market="binance_spot", mode_provider=lambda: mode[0],
    )
    again = OfflineMarketProjectionCursor(reopened)
    assert again.projections("indicator") == [expected]
    assert claim(again).event.sequence == 101
    # No EventBus publisher is wired; committing a projection is not consumer ACK.


def test_asof_claim_never_leaps_over_earlier_unavailable_event(tmp_path):
    cursor, _, _ = ready(tmp_path, receipts=(BASE + 300, BASE + 200))
    assert claim(cursor, now=BASE + 250, as_of_ms=BASE + 250) is None
    first = claim(cursor, now=BASE + 350, as_of_ms=BASE + 350)
    assert first.event.sequence == 100
    commit(cursor, first, now=BASE + 351)
    second = claim(cursor, now=BASE + 352, as_of_ms=BASE + 250)
    assert second.event.sequence == 101


def test_mode_transition_blocks_claim_commit_and_inspection(tmp_path):
    mode = [QuantMode.SIMULATION]
    cursor, _, _ = ready(tmp_path, mode=mode)
    ticket = claim(cursor)
    mode[0] = QuantMode.REALITY
    with pytest.raises(RuntimeError, match="SIMULATION"):
        claim(cursor)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        commit(cursor, ticket)
    with pytest.raises(RuntimeError, match="SIMULATION"):
        cursor.projections("indicator")
    mode[0] = QuantMode.SIMULATION
    commit(cursor, ticket)
    assert cursor.cursor_position("indicator") == ticket.ingest_id


def test_bad_lease_parameters_fail_closed(tmp_path):
    cursor, _, _ = ready(tmp_path)
    for c in ("", "has space", "evil/consumer"):
        with pytest.raises(ValueError):
            claim(cursor, c)
    for ms in (0, True, 300001):
        with pytest.raises(ValueError):
            claim(cursor, lease_ms=ms)
    with pytest.raises(ValueError, match="as_of"):
        claim(cursor, now=BASE + 300, as_of_ms=BASE + 301)
    with pytest.raises(ValueError):
        claim(cursor, now=True)


def test_corrupted_market_row_fails_before_consumer_lease(tmp_path):
    cursor, journal, _ = ready(tmp_path)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE offline_market_events SET frame_json=? WHERE sequence=100",
            ('{"e":"aggTrade","a":100}',),
        )
    with pytest.raises(MarketEventError):
        claim(cursor)
    with sqlite3.connect(journal.path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM offline_market_delivery_claims"
        ).fetchone()[0] == 0


def test_projection_tamper_is_rejected_including_changed_availability(tmp_path):
    cursor, journal, _ = ready(tmp_path)
    ticket = claim(cursor)
    commit(cursor, ticket)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE offline_market_projections SET value_json=?",
            ('{"mid":"999"}',),
        )
    with pytest.raises(MarketEventError):
        cursor.projections("indicator")
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE offline_market_projections SET value_json=?, available_at_ms=?",
            ('{"mid":"100"}', BASE + 90),
        )
    with pytest.raises(MarketEventError):
        cursor.projections("indicator")


def test_tampered_source_identity_refused_even_with_intact_projection(tmp_path):
    cursor, journal, _ = ready(tmp_path)
    ticket = claim(cursor)
    commit(cursor, ticket)
    with sqlite3.connect(journal.path) as conn:
        conn.execute(
            "UPDATE offline_market_events SET event_id=? WHERE sequence=100",
            ("forged-origin",),
        )
    with pytest.raises(MarketEventError):
        cursor.projections("indicator")


def test_no_skip_while_there_is_an_active_lease(tmp_path):
    cursor, _, _ = ready(tmp_path)
    claim(cursor, lease_ms=30_000)
    assert claim(cursor, now=BASE + 400) is None
    assert cursor.cursor_position("indicator") == 0
    assert cursor.projections("indicator") == []
