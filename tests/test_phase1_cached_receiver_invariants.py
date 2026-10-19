"""Receiver cache is child-owned, FULL-durable and never implicit/public."""
from __future__ import annotations

import sqlite3
import pytest

from core.mode import QuantMode
from ci.phase1_offline_burst_probe import _source,_snapshot
from infrastructure.market_cross_process_delivery import OfflineIndependentProjectionReceiver
from infrastructure.spot_forward_verified_research import observe_forward_spot_research


def test_cached_receiver_sqlite_keeps_wal_full_and_closes(tmp_path):
    db=(tmp_path/"independent_receiver.sqlite").resolve()
    receiver=OfflineIndependentProjectionReceiver(
        db,mode_provider=lambda:QuantMode.SIMULATION,
        reuse_connection=True,
    )
    with receiver._connect() as one:
        assert one.execute("PRAGMA journal_mode").fetchone()[0].lower()=="wal"
        assert one.execute("PRAGMA synchronous").fetchone()[0]==2
    with receiver._connect() as two:
        assert one is two
    receiver.close()
    with sqlite3.connect(db.as_uri()+"?mode=ro",uri=True) as read:
        assert read.execute("PRAGMA integrity_check").fetchone()[0]=="ok"


@pytest.mark.asyncio
async def test_cached_receiver_real_hmac_child_offline_fixture(tmp_path):
    root=(tmp_path/"receiver_cached_child").resolve()
    result=await observe_forward_spot_research(
        output_dir=root,session_id="rec_cached_receiver_child",
        stream=_source(30,60),snapshot_fetch=_snapshot,
        max_frames=30,timeout_s=30,persistent_receiver=True,
        spooled_in_session=True,
        experiment_reuse_derived_connections=True,
        experiment_reuse_producer_connections=True,
        experiment_reuse_receiver_connection=True,
    )
    assert result.session_complete and result.original_records==31
    assert result.spool_original_cursor==31
    assert result.forward_checkpoints==result.receiver_receipts
    assert result.receiver_process_starts==1
    assert result.orders_submitted==0
    with sqlite3.connect((root/"independent_receiver.sqlite").as_uri()+"?mode=ro",uri=True) as read:
        assert read.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert read.execute(
            "SELECT COUNT(*) FROM offline_independent_projection_receipts"
        ).fetchone()[0]==result.receiver_receipts


@pytest.mark.asyncio
async def test_receiver_cache_denied_without_approved_profile(tmp_path):
    root=(tmp_path/"denied").resolve()
    with pytest.raises(PermissionError,match="receiver connection cache"):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_receiver_unreviewed",
            stream=_source(30,60),snapshot_fetch=_snapshot,
            max_frames=30,persistent_receiver=True,spooled_in_session=True,
            experiment_reuse_receiver_connection=True,
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_receiver_cache_denied_for_public_without_opt_in(tmp_path):
    root=(tmp_path/"denied_public").resolve()
    with pytest.raises(PermissionError):
        await observe_forward_spot_research(
            output_dir=root,session_id="rec_receiver_public_denied",
            allow_public_network=True,source="binance_public_simulation",
            max_frames=30,persistent_receiver=True,spooled_in_session=True,
            experiment_reuse_derived_connections=True,
            experiment_reuse_producer_connections=True,
            experiment_reuse_receiver_connection=True,
        )
    assert not root.exists()
