"""P1-12 public co-run acceptance: no paid APIs, network or orders in CI."""
from __future__ import annotations

import copy
import json

import pytest

from ci.phase1_public_corun_hmac_acceptance import (
    PASS, validate_public_proof, run_public, main,
)
from ci.phase1_rotating_hmac_audit import main as audit_main

DURATION = 120
FRAMES = 450


@pytest.fixture
def accepted():
    result = {
        "schema": "econith-phase1-rotating-hmac-corun-v1",
        "verdict": "PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY",
        "mode": "SIMULATION",
        "public_read_only_market_source": True,
        "source_reader_attached_during_hmac": True,
        "source_socket_count_observed": 1,
        "per_segment_postseal_delivery": True,
        "postseal_handoff_only": False,
        "full_continuous_market_hmac_certified": False,
        "unobserved_venue_history_certified": False,
        "default_ingress_cutover": False,
        "institutional_phase1_complete": False,
        "live_orders_authorized": False,
        "orders_submitted": 0,
        "independent_local_audit_passed": True,
        "receiver_batch_size": 8,
        "receiver_connection_reuse": True,
        "segments": [{"index": 0}, {"index": 1}, {"index": 2}],
        "source_ws_frames": 1300,
        "originals": 1303,
        "reconstructed_projections": 1280,
        "independent_receiver_receipts": 1280,
        "forward_checkpoints": 1280,
        "named_subscriber_receipts": 2400,
        "derived_receipts": 1200,
        "concurrent_segments_completed": 2,
        "source_queue_high_watermark": 4,
        "adapter_queue_capacity": 128,
        "segment_queue_capacity": 2,
        "segment_backlog_peak": 2,
        "outstanding_hmac_segments_peak": 3,
        "source_original_commit_age_ms_max": 805,
        "segment_handoff_lag_max_ms": 2400,
        "source_socket_observed_duration_s": 120.01,
        "independently_audited_reconstructed_projections": 1280,
    }
    proof = {
        "verdict": "PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY",
        "orders_submitted": 0,
        "HMAC_IPC_channel_independently_reattested": False,
        "continuous_live_hmac_verified": False,
        "exchange_history_completeness_verified": False,
        "all_checkpoint_chain_hashes_verified": True,
        "segments": 3,
        "raw_original_events": 1303,
        "source_projections": 1280,
        "receiver_owned_receipts": 1280,
        "forward_checkpoints": 1280,
    }
    return result, proof


def check(record, audit):
    return validate_public_proof(
        record, audit, duration_s=DURATION,
        frames_per_segment=FRAMES, max_handoff_age_s=30,
    )


def test_bound_public_reader_hmac_and_checkpoint_consistency(accepted):
    result, proof = accepted
    summary = check(result, proof)
    assert summary["schema"] == "econith-p1-public-rotating-hmac-acceptance-v1"
    assert summary["verdict"] == PASS
    assert summary["source_ws_frames"] == 1300
    assert summary["originals"] == 1303
    assert summary["independent_receiver_receipts"] == 1280
    assert summary["forward_checkpoints"] == 1280
    assert summary["receiver_batch_size"] == 8
    assert summary["continuous_event_to_hmac_verified"] is False
    assert summary["exchange_source_completeness_verified"] is False
    assert summary["institutional_phase1_complete"] is False
    assert summary["orders_submitted"] == 0


@pytest.mark.parametrize("patch", [
    ("verdict", "FAILED_OR_QUARANTINED"),
    ("public_read_only_market_source", False),
    ("source_reader_attached_during_hmac", False),
    ("source_socket_count_observed", 2),
    ("full_continuous_market_hmac_certified", True),
    ("default_ingress_cutover", True),
    ("orders_submitted", 1),
    ("orders_submitted", False),
    ("independent_local_audit_passed", False),
    ("source_queue_high_watermark", 64),
    ("source_original_commit_age_ms_max", 5001),
    ("segment_backlog_peak", 3),
    ("segment_handoff_lag_max_ms", 30001),
    ("source_socket_observed_duration_s", 50),
    ("independent_receiver_receipts", 1279),
    ("forward_checkpoints", 1279),
    ("reconstructed_projections", 0),
    ("named_subscriber_receipts", 2401),
    ("independently_audited_reconstructed_projections", 1279),
    ("segments", [{"index": 0}]),
    ("receiver_connection_reuse", False),
])
def test_untrusted_or_quarantined_producer_never_passes(accepted, patch):
    source, proof = copy.deepcopy(accepted)
    source[patch[0]] = patch[1]
    with pytest.raises(ValueError):
        check(source, proof)


@pytest.mark.parametrize("patch", [
    ("verdict", "PASSED_LOCAL_POSTSEAL_RECEIVER_CHECKPOINT_CHAIN_ONLY"),
    ("continuous_live_hmac_verified", True),
    ("exchange_history_completeness_verified", True),
    ("HMAC_IPC_channel_independently_reattested", True),
    ("all_checkpoint_chain_hashes_verified", False),
    ("source_projections", 1279),
    ("receiver_owned_receipts", 1279),
    ("forward_checkpoints", 1279),
    ("segments", 2),
])
def test_forged_or_incomplete_independent_audit_refused(accepted, patch):
    source, proof = copy.deepcopy(accepted)
    proof[patch[0]] = patch[1]
    with pytest.raises(ValueError):
        check(source, proof)


@pytest.mark.asyncio
@pytest.mark.parametrize("duration,frames,lag", [
    (89, 450, 30), (151, 450, 30),
    (120, 249, 30), (120, 651, 30),
    (120, 450, 31), (120, 450, 0),
    (float("nan"), 450, 30), (120, True, 30),
])
async def test_invalid_public_scope_rejected_before_network_or_files(
    tmp_path, duration, frames, lag,
):
    dest = (tmp_path / "unused").resolve()
    with pytest.raises(ValueError):
        await run_public(dest, duration_s=duration,
                         frames_per_segment=frames,
                         max_handoff_age_s=lag)
    assert not dest.exists()


def test_cli_requires_all_three_consents_without_invoking_market(tmp_path):
    out = (tmp_path / "no_capture").resolve()
    argv = ["--output-dir", str(out)]
    for flags in (
        [],
        ["--allow-public-market-data"],
        ["--allow-public-market-data", "--enable-independent-hmac-receiver"],
        ["--allow-public-market-data", "--acknowledge-not-continuous-24h"],
    ):
        with pytest.raises(SystemExit) as err:
            main(argv + flags)
        assert err.value.code == 2
        assert not out.exists()


def test_public_cli_failure_does_not_claim_success_or_retry(monkeypatch, tmp_path, capsys):
    import ci.phase1_public_corun_hmac_acceptance as module
    calls = [0]

    async def network_fault(root, **kwargs):
        calls[0] += 1
        raise RuntimeError("injected source disconnect")

    monkeypatch.setattr(module, "run_public", network_fault)
    argv = [
        "--output-dir", str((tmp_path / "report").resolve()),
        "--allow-public-market-data",
        "--enable-independent-hmac-receiver",
        "--acknowledge-not-continuous-24h",
    ]
    assert module.main(argv) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["verdict"] == "REFUSED_OR_QUARANTINED"
    assert report["orders_submitted"] == 0
    assert calls == [1]


@pytest.mark.asyncio
async def test_real_runner_always_invokes_separate_independent_original_audit(
    monkeypatch, tmp_path, accepted,
):
    import ci.phase1_public_corun_hmac_acceptance as module
    source, proof = copy.deepcopy(accepted)
    calls = []

    async def fake_source(root, **params):
        calls.append("source")
        assert params["explicit_public_research_approval"] is True
        assert params["receiver_batch_size"] == 8
        assert params["reuse_receiver_connection"] is True
        return source

    def fake_audit(root, *, co_run):
        calls.append("independent_audit")
        assert co_run is True
        return proof

    monkeypatch.setattr(module, "co_run_rotating_hmac", fake_source)
    monkeypatch.setattr(module, "independently_inspect_hmac", fake_audit)
    result = await module.run_public(
        (tmp_path / "fresh").resolve(),
        duration_s=120, frames_per_segment=450, max_handoff_age_s=30,
    )
    assert result["verdict"] == PASS
    assert calls == ["source", "independent_audit"]

    calls.clear()
    source["verdict"] = "FAILED_OR_QUARANTINED"
    with pytest.raises(ValueError, match="quarantine"):
        await module.run_public(
            (tmp_path / "fresh2").resolve(),
            duration_s=120, frames_per_segment=450, max_handoff_age_s=30,
        )
    assert calls == ["source"]


def test_existing_readonly_hmac_auditor_cli_supports_explicit_corun_mode(
    monkeypatch, tmp_path, capsys,
):
    import ci.phase1_rotating_hmac_audit as module
    used = []

    def pretend(root, *, co_run=False):
        used.append(co_run)
        return {
            "verdict": (
                "PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY" if co_run
                else "PASSED_LOCAL_POSTSEAL_RECEIVER_CHECKPOINT_CHAIN_ONLY"
            ),
            "orders_submitted": 0,
        }

    monkeypatch.setattr(module, "inspect", pretend)
    assert audit_main(["--root", str(tmp_path), "--co-run"]) == 0
    assert json.loads(capsys.readouterr().out)["orders_submitted"] == 0
    assert audit_main(["--root", str(tmp_path)]) == 0
    assert used == [True, False]
