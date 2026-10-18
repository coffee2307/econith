"""Source-first durable spool never promotes an unsealed or damaged source."""
from __future__ import annotations

import json
import sqlite3

import pytest

from ci.phase1_offline_burst_probe import _source, _snapshot
from infrastructure.spot_bounded_durable_spool import (
    capture_then_replay_sealed_spool,
)
from tests.test_spot_forward_verified_research import source, _NOW


@pytest.mark.asyncio
async def test_source_first_spool_short_burst_seals_and_reconciles(tmp_path):
    root = (tmp_path / "sealed").resolve()
    report = await capture_then_replay_sealed_spool(
        output_dir=root, session_id="rec_test_spool",
        stream=_source(30, 3), snapshot_fetch=_snapshot,
        max_frames=30, timeout_s=30,
    )
    assert report.mode == "SIMULATION"
    assert report.originals == 31
    assert report.independent_receiver_receipts == report.reconstructed_intents == 29
    assert report.checkpoints == report.reconstructed_intents
    assert report.named_receipts == report.derived_receipts * 2
    assert report.source_session_closed
    assert report.sealed_source_only and not report.continuous_live_market
    assert not report.in_session_market_publication
    assert not report.history_continuity_verified
    assert report.orders_submitted == 0 and not report.execution_started
    with sqlite3.connect((root / "original.sqlite").as_uri() + "?mode=ro",
                         uri=True) as db:
        assert db.execute("SELECT COUNT(*) FROM offline_spot_capture_records").fetchone()[0] == 31


@pytest.mark.asyncio
async def test_source_first_spool_rejects_gap_without_forward_side_effects(tmp_path):
    root = (tmp_path / "bad").resolve()
    with pytest.raises(Exception, match="trade sequence gap"):
        await capture_then_replay_sealed_spool(
            output_dir=root, session_id="rec_bad_spool",
            stream=source(_NOW(), gap=True), snapshot_fetch=_snapshot,
            max_frames=20, timeout_s=20,
        )
    assert (root/"original.sqlite").is_file()
    assert not (root/"forward_outbox.sqlite").exists()
    assert not (root/"independent_receiver.sqlite").exists()
    assert not (root/"derived.sqlite").exists()


@pytest.mark.asyncio
async def test_spool_refuses_reuse_and_unsafe_capacity(tmp_path):
    root = (tmp_path/"reuse").resolve()
    root.mkdir()
    with pytest.raises(ValueError, match="invalid bounded"):
        await capture_then_replay_sealed_spool(
            output_dir=root, session_id="rec_reuse",
            stream=_source(30,3), snapshot_fetch=_snapshot,
            max_frames=30,
        )
    second=(tmp_path/"unsafe").resolve()
    with pytest.raises(ValueError, match="invalid bounded"):
        await capture_then_replay_sealed_spool(
            output_dir=second, session_id="rec_bad_capacity",
            stream=_source(30,3), snapshot_fetch=_snapshot,
            max_frames=30, queue_capacity=129,
        )
    assert not second.exists()


@pytest.mark.asyncio
async def test_source_spool_budget_fail_closed_before_any_receiver_effect(tmp_path):
    root=(tmp_path/"small_quota").resolve()
    with pytest.raises(RuntimeError,match="spool byte budget exceeded"):
        await capture_then_replay_sealed_spool(
            output_dir=root,session_id="rec_quota_exceeded",
            stream=_source(30,3),snapshot_fetch=_snapshot,
            max_frames=30,timeout_s=30,max_source_raw_bytes=1024,
        )
    assert (root/"original.sqlite").is_file()
    assert not (root/"independent_receiver.sqlite").exists()
    assert not (root/"forward_outbox.sqlite").exists()


@pytest.mark.asyncio
async def test_public_source_requires_explicit_exclusive_opt_in(tmp_path):
    no_mode=(tmp_path/"no_source").resolve()
    with pytest.raises(ValueError,match="exclusive spool source"):
        await capture_then_replay_sealed_spool(
            output_dir=no_mode,session_id="rec_missing_policy",
        )
    assert not no_mode.exists()
    mixed=(tmp_path/"mixed").resolve()
    with pytest.raises(ValueError,match="exclusive spool source"):
        await capture_then_replay_sealed_spool(
            output_dir=mixed,session_id="rec_mixed_policy",
            stream=_source(30,3),snapshot_fetch=_snapshot,
            allow_public_network=True,
        )
    assert not mixed.exists()
