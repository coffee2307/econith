"""Per-committed-event forward research: prove as-of and independent ACK."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import pytest

from infrastructure.spot_forward_verified_research import (
    ForwardSpotResearchError, observe_forward_spot_research,
)

_NOW = lambda: time.time_ns() // 1_000_000


def frame(data):
    name = (
        "btcusdt@aggTrade" if data["e"] == "aggTrade"
        else "btcusdt@depth@100ms"
    )
    return json.dumps({"stream": name, "data": data}).encode("utf-8")


def trade(number, ts):
    return {"e":"aggTrade", "s":"BTCUSDT", "E":ts, "T":ts - 10,
            "a":number, "f":number, "l":number,
            "p":"101.25", "q":"0.2", "m":False}


def depth(first, final, ts):
    return {"e":"depthUpdate", "s":"BTCUSDT", "E":ts,
            "U":first, "u":final, "b":[["100","4"]], "a":[]}


async def snapshot(_symbol, _limit):
    await asyncio.sleep(0)
    return json.dumps({
        "lastUpdateId":100,
        "bids":[["100","3"]], "asks":[["102","5"]],
    }).encode("utf-8")


def source(now, *, gap=False, until=None):
    async def stream():
        # This 499 trade cannot inherit a snapshot received in the future.
        yield frame(trade(499, now))
        yield frame(depth(100, 102, now))
        await asyncio.sleep(0.15)  # let REST request reach capture FIFO
        yield frame(trade(500, now))
        yield frame(depth(103, 103, now))
        if until is not None:
            await until.wait()
        yield frame(trade(502 if gap else 501, now))
    return stream()


@pytest.mark.asyncio
async def test_actual_subprocess_receiver_before_in_session_research_publication(tmp_path):
    root = (tmp_path / "forward").resolve()
    release = asyncio.Event()
    now = _NOW()
    task = asyncio.create_task(observe_forward_spot_research(
        output_dir=root, session_id="rec_forward01",
        stream=source(now, until=release), snapshot_fetch=snapshot,
        max_frames=20, timeout_s=20,
    ))
    try:
        # Before closing the source, a real separate child must already have
        # persisted 3 envelopes (initial synced book, trade500, book103).
        deadline = asyncio.get_running_loop().time() + 12
        while True:
            db = root / "independent_receiver.sqlite"
            if db.exists():
                try:
                    with sqlite3.connect(db) as conn:
                        count = conn.execute(
                            "SELECT COUNT(*) FROM offline_independent_projection_receipts"
                        ).fetchone()[0]
                except sqlite3.OperationalError:
                    count = 0
                if count >= 3:
                    break
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError("receiver never ACKed within open source session")
            await asyncio.sleep(0.02)
        with sqlite3.connect(root / "original.sqlite") as conn:
            original = conn.execute(
                "SELECT closed_at_ms FROM offline_spot_capture_sessions"
            ).fetchone()
        assert original[0] is None  # independent ACK while capture remains OPEN
    finally:
        release.set()
    status = await task
    assert status.mode == "SIMULATION"
    assert status.session_id == "rec_forward01"
    assert status.original_records == 6  # 5 WS + 1 REST snapshot
    assert status.verified_trades == 3
    assert status.pre_sync_trades_not_forwarded == 1
    assert status.forwarded_trades == 2
    assert status.forwarded_books == 2
    assert status.receiver_receipts == 4
    assert status.primary_derived_receipts == 6
    assert status.named_derived_receipts == 12
    assert status.book_final_update_id == 103
    assert status.all_receipts_independent
    assert status.publication_in_session
    assert status.research_only
    assert status.session_complete
    assert not status.history_continuity_verified
    assert not status.continuous_live_market
    assert not status.execution_started
    assert status.orders_submitted == 0
    with sqlite3.connect(root / "independent_receiver.sqlite") as conn:
        rows=conn.execute("""
            SELECT ingest_id,projection_json FROM offline_independent_projection_receipts
            ORDER BY ingest_id
        """).fetchall()
    assert len(rows) == 4
    events = [json.loads(x[1]) for x in rows]
    assert [x["topic"] for x in events] == [
        "md.depth", "md.aggTrade", "md.depth", "md.aggTrade",
    ]
    assert events[0]["verified_asof_book_id"] == 102
    assert events[2]["verified_asof_book_id"] == 103
    assert all(x["source_available_at_ms"] > 0 for x in events)
    assert all(x["source_record_id"].startswith("rec_forward01:") for x in events)


@pytest.mark.asyncio
async def test_gap_mid_session_aborts_and_never_certifies_partial_forward(tmp_path):
    root = (tmp_path / "gap").resolve()
    with pytest.raises(Exception, match="trade sequence gap"):
        await observe_forward_spot_research(
            output_dir=root, session_id="rec_forward_gap",
            stream=source(_NOW(), gap=True), snapshot_fetch=snapshot,
            max_frames=20, timeout_s=20,
        )
    with sqlite3.connect(root / "original.sqlite") as conn:
        row=conn.execute(
            "SELECT closed_at_ms FROM offline_spot_capture_sessions"
        ).fetchone()
    assert row[0] is not None  # failure is captured as a closed, unusable session
    assert not (root/"forward_complete.json").exists()
    with sqlite3.connect(root / "independent_receiver.sqlite") as conn:
        c=conn.execute(
            "SELECT COUNT(*) FROM offline_independent_projection_receipts"
        ).fetchone()[0]
    assert c >= 1  # already observed research receipts cannot be rolled back


@pytest.mark.asyncio
async def test_explicit_public_source_policy_denies_implicit_network_and_mix(tmp_path):
    missing = (tmp_path/"unapproved").resolve()
    with pytest.raises(PermissionError):
        await observe_forward_spot_research(
            output_dir=missing, session_id="rec_unsafe",
        )
    assert not missing.exists()
    with pytest.raises(PermissionError):
        await observe_forward_spot_research(
            output_dir=missing, session_id="rec_unsafe",
            stream=source(_NOW()), snapshot_fetch=snapshot,
            allow_public_network=True,
        )
    assert not missing.exists()
    with pytest.raises(ValueError):
        await observe_forward_spot_research(
            output_dir=missing, session_id="rec_unsafe",
            stream=source(_NOW()), snapshot_fetch=snapshot, max_frames=1,
        )
    assert not missing.exists()


@pytest.mark.asyncio
async def test_trade_with_stale_book_refused_after_original_committed(tmp_path):
    root = (tmp_path / "stale_book").resolve()
    with pytest.raises(ForwardSpotResearchError, match="stale synchronized book"):
        await observe_forward_spot_research(
            output_dir=root, session_id="rec_forward_stale",
            stream=source(_NOW()), snapshot_fetch=snapshot,
            max_frames=20, timeout_s=20,
            # The fixture holds the first post-snapshot trade for 150ms.
            # A 100ms research freshness budget must deny it.
            max_book_staleness_ms=100,
        )
    with sqlite3.connect(root/"original.sqlite") as conn:
        row=conn.execute(
            "SELECT closed_at_ms FROM offline_spot_capture_sessions"
        ).fetchone()
    assert row[0] is not None
    assert not (root/"forward_complete.json").exists()
