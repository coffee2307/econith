"""Extra safety gates for isolated experimental producer SQLite connection reuse."""
from __future__ import annotations

import sqlite3

import pytest

from core.mode import QuantMode
from infrastructure.spot_forward_outbox import OfflineForwardSpotOutbox
from infrastructure.spot_forward_checkpoints import OfflineForwardSessionCheckpoints
from infrastructure.spot_forward_verified_research import observe_forward_spot_research


def test_producer_cached_sqlite_maintains_wal_full_and_independent_files(tmp_path):
    provider=lambda: QuantMode.SIMULATION
    outbox=OfflineForwardSpotOutbox(
        (tmp_path/"producer.sqlite").resolve(),
        session_id="rec_cached_prod_test",mode_provider=provider,
        reuse_connection=True,
    )
    checkpoints=OfflineForwardSessionCheckpoints(
        (tmp_path/"checkpoints.sqlite").resolve(),
        session_id="rec_cached_prod_test",mode_provider=provider,
        reuse_connection=True,
    )
    try:
        with outbox._connect() as db1:
            assert db1.execute("PRAGMA synchronous").fetchone()[0]==2
            assert db1.execute("PRAGMA journal_mode").fetchone()[0].lower()=="wal"
        with outbox._connect() as db2:
            assert db1 is db2
        with checkpoints._db() as ck1:
            assert ck1.execute("PRAGMA synchronous").fetchone()[0]==2
            assert ck1.execute("PRAGMA journal_mode").fetchone()[0].lower()=="wal"
        with checkpoints._db() as ck2:
            assert ck1 is ck2
        assert ck1 is not db1
    finally:
        outbox.close()
        checkpoints.close()
    with sqlite3.connect((tmp_path/"producer.sqlite").resolve().as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    with sqlite3.connect((tmp_path/"checkpoints.sqlite").resolve().as_uri()+"?mode=ro",uri=True) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


@pytest.mark.asyncio
async def test_public_producer_cache_needs_explicit_experimental_gate(tmp_path):
    root=(tmp_path/"public_denied").resolve()
    with pytest.raises(PermissionError,match="separately opted-in"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_cache_public_denied",
            allow_public_network=True,source="binance_public_simulation",
            persistent_receiver=True,spooled_in_session=True,
            experiment_reuse_derived_connections=True,
            experiment_reuse_producer_connections=True,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_offline_cached_producer_refuses_absent_derived_review(tmp_path):
    root=(tmp_path/"unreviewed").resolve()
    from ci.phase1_offline_burst_probe import _source,_snapshot
    with pytest.raises(PermissionError,match="separately opted-in"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_cache_no_derived",
            stream=_source(30,60),snapshot_fetch=_snapshot,
            max_frames=30,persistent_receiver=True,spooled_in_session=True,
            experiment_reuse_producer_connections=True,
        )
    assert not root.exists()
