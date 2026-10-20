"""P1-13: measure time after durable HMAC checkpoint, not EventBus admission.

All fixture tests are offline, with an independent SQLite read-only
reconciliation. Time source remains unauthenticated local host wall clock.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ci.phase1_offline_burst_probe import _source, _snapshot
from infrastructure.spot_checkpoint_delivery_probe import (
    BoundedCheckpointAgeProbe, CheckpointAgeError, inspect, PASS,
)
from infrastructure.spot_forward_verified_research import (
    observe_forward_spot_research,
)


@pytest.mark.asyncio
async def test_real_in_session_hmac_checkpoint_bound_to_all_source_events(tmp_path):
    root = (tmp_path / "finite").resolve()
    status = await observe_forward_spot_research(
        output_dir=root, session_id="rec_p113_complete",
        stream=_source(30, 60), snapshot_fetch=_snapshot,
        max_frames=30, timeout_s=30, persistent_receiver=True,
        spooled_in_session=True, max_projector_lag_ms=5000,
        experiment_reuse_derived_connections=True,
        experiment_checkpoint_completion_timing=True,
    )
    assert status.session_complete is True
    assert status.publication_in_session is True
    assert status.source_to_checkpoint_samples == status.receiver_receipts
    assert status.source_to_checkpoint_max_ms is not None
    assert 0 <= status.source_to_checkpoint_max_ms <= 5000
    audit = inspect(root)
    assert audit["verdict"] == PASS
    assert audit["source_to_after_checkpoint_samples"] == status.receiver_receipts
    assert audit["max_source_to_after_checkpoint_ms"] == status.source_to_checkpoint_max_ms
    assert audit["independent_local_ledger_binding_verified"] is True
    assert audit["clock_samples_independently_attested"] is False
    assert audit["source_venue_completeness_verified"] is False
    assert audit["orders_submitted"] == 0


@pytest.mark.asyncio
async def test_corrupting_local_age_sample_rejected_against_checkpoint_ledger(tmp_path):
    root = (tmp_path / "sample_mutated").resolve()
    status = await observe_forward_spot_research(
        output_dir=root, session_id="rec_p113_tamper",
        stream=_source(25, 75), snapshot_fetch=_snapshot,
        max_frames=25, timeout_s=30,
        persistent_receiver=True, spooled_in_session=True,
        experiment_reuse_derived_connections=True,
        experiment_checkpoint_completion_timing=True,
    )
    assert status.receiver_receipts > 0
    f = root / "checkpoint_delivery_ages.json"
    doc = json.loads(f.read_text())
    doc["rows"][0]["receiver_receipt_id"] = "f" * 64
    f.write_text(json.dumps(doc))
    with pytest.raises(CheckpointAgeError, match="timing source/receiver"):
        inspect(root)


@pytest.mark.asyncio
async def test_fake_after_checkpoint_delay_refuses_even_if_eventbus_admission_was_fresh(
    tmp_path, monkeypatch,
):
    # The prior admission-age gate runs *before* the event bus; this injection
    # simulates a stalled derived subscriber/checkpoint completion clock.
    original = BoundedCheckpointAgeProbe.observe

    def exceed(self, envelope, receipt_id, checkpoint, *, observed_after_commit_ms=None):
        return original(
            self, envelope, receipt_id, checkpoint,
            observed_after_commit_ms=envelope.available_at_ms + self.budget_ms + 1,
        )

    monkeypatch.setattr(BoundedCheckpointAgeProbe, "observe", exceed)
    root = (tmp_path / "late_after_ack").resolve()
    with pytest.raises(CheckpointAgeError, match="exceeded"):
        await observe_forward_spot_research(
            output_dir=root, session_id="rec_p113_delayed",
            stream=_source(25, 75), snapshot_fetch=_snapshot,
            max_frames=25, timeout_s=30,
            persistent_receiver=True, spooled_in_session=True,
            experiment_reuse_derived_connections=True,
            experiment_checkpoint_completion_timing=True,
        )
    assert (root / "original.sqlite").is_file()
    assert not (root / "checkpoint_delivery_ages.json").exists()
    with pytest.raises(CheckpointAgeError):
        inspect(root)


@pytest.mark.asyncio
async def test_timing_requires_spool_and_authenticated_receiver_before_files(tmp_path):
    for opts in (
        {"spooled_in_session": False, "persistent_receiver": True},
        {"spooled_in_session": True, "persistent_receiver": False},
    ):
        root = (tmp_path / ("nospool" if not opts["spooled_in_session"] else "noreceiver")).resolve()
        with pytest.raises(PermissionError, match="checkpoint completion probe"):
            await observe_forward_spot_research(
                output_dir=root, session_id="rec_p113_reject",
                stream=_source(20, 10), snapshot_fetch=_snapshot,
                max_frames=20, timeout_s=20,
                experiment_checkpoint_completion_timing=True,
                **opts,
            )
        assert not root.exists()


@pytest.mark.parametrize("age", [-1, 1001, 3.5, None, True])
def test_bounded_age_refuses_future_stale_and_noninteger_ms(tmp_path, age):
    root = (tmp_path / "raw_probes").resolve()
    root.mkdir()
    tool = BoundedCheckpointAgeProbe(
        root / "checkpoint_delivery_ages.json", budget_ms=1000,
    )
    from infrastructure.market_cross_process_delivery import ProjectionEnvelope
    env = ProjectionEnvelope(
        source_consumer="forward_spot", receiver="read_only_market",
        ingest_id=3, event_id="md:forward:rec_test:3",
        available_at_ms=300000,
        projection_json="{}", projection_fingerprint="a"*64,
    )
    cp = SimpleNamespace(
        checkpointed_events=1, last_capture_id=3,
        last_available_at_ms=300000, tail_sha256="b"*64,
    )
    with pytest.raises(CheckpointAgeError):
        tool.observe(
            env, "c"*64, cp,
            observed_after_commit_ms=(300000 + age if type(age) is int else age),
        )
    assert tool.rows == []
    assert not tool.path.exists()


def test_probe_cannot_rewrite_a_sealed_latency_report(tmp_path):
    root = (tmp_path / "one").resolve()
    root.mkdir()
    tool = BoundedCheckpointAgeProbe(
        root / "checkpoint_delivery_ages.json", budget_ms=1000,
    )
    from infrastructure.market_cross_process_delivery import ProjectionEnvelope
    env = ProjectionEnvelope(
        source_consumer="forward_spot", receiver="read_only_market",
        ingest_id=1, event_id="md:forward:rec_test:1",
        available_at_ms=1000, projection_json="{}", projection_fingerprint="a"*64,
    )
    cp = SimpleNamespace(checkpointed_events=1, last_capture_id=1,
                         last_available_at_ms=1000, tail_sha256="b"*64)
    assert tool.observe(env, "c"*64, cp, observed_after_commit_ms=1020) == 20
    assert tool.seal()["age_samples"] == 1
    with pytest.raises(FileExistsError):
        tool.seal()


def test_untrusted_timing_file_without_originals_cannot_self_certify(tmp_path):
    root = (tmp_path / "missing_ledgers").resolve()
    root.mkdir()
    tool = BoundedCheckpointAgeProbe(
        root / "checkpoint_delivery_ages.json", budget_ms=1000,
    )
    from infrastructure.market_cross_process_delivery import ProjectionEnvelope
    env = ProjectionEnvelope(
        source_consumer="forward_spot", receiver="read_only_market",
        ingest_id=1, event_id="md:forward:rec_test:1",
        available_at_ms=1000, projection_json="{}", projection_fingerprint="a"*64,
    )
    cp = SimpleNamespace(checkpointed_events=1, last_capture_id=1,
                         last_available_at_ms=1000, tail_sha256="b"*64)
    tool.observe(env, "c"*64, cp, observed_after_commit_ms=1001)
    tool.seal()
    with pytest.raises(CheckpointAgeError, match="missing or aliased"):
        inspect(root)
