"""P1-12: bounded public Spot source -> rotating originals -> HMAC checkpoint trial.

Only this explicitly opted-in research entrypoint may invoke public market
source co-running. It never opens private exchange sessions, sends orders,
enables default runtime ingress or claims 24h/HMAC-per-event coverage.
A source-producer PASS is insufficient without independent cross-ledger audit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3

from ci.phase1_public_spot_acceptance import measure_public_clock
from ci.phase1_rotating_corun_hmac import co_run_rotating_hmac
from ci.phase1_rotating_hmac_audit import inspect as independently_inspect_hmac
from infrastructure.spot_public_pinned_lease import open_pinned_public_spot_lease

PASS = "PASSED_FINITE_PUBLIC_SOURCE_AND_POSTSEAL_HMAC_REAUDIT_ONLY"
SOURCE_PASS = "PASSED_BOUNDED_CO_RUNNING_HMAC_SEGMENTS_RESEARCH_ONLY"
AUDIT_PASS = "PASSED_LOCAL_CORUN_RECEIVER_CHECKPOINT_CHAIN_ONLY"


def _bounded(n: object, *, minimum: float, maximum: float) -> bool:
    return type(n) in (int, float) and minimum <= n <= maximum


def validate_public_proof(result: dict, proof: dict, *, duration_s: float,
                          frames_per_segment: int,
                          max_handoff_age_s: float) -> dict:
    """Fail closed on missing or unsafe producer/independent audit evidence.

    This is an integrity/cost gate, not a claim of exchange signed data
    completeness or uninterrupted per-event HMAC transmission.
    """
    if type(result) is not dict or type(proof) is not dict:
        raise ValueError("public market/hmac result must be an object")
    segments = result.get("segments")
    projected = result.get("reconstructed_projections")
    frames = result.get("source_ws_frames")
    originals = result.get("originals")
    if not (
        result.get("schema") == "econith-phase1-rotating-hmac-corun-v1"
        and result.get("verdict") == SOURCE_PASS
        and result.get("public_read_only_market_source") is True
        and result.get("mode") == "SIMULATION"
        and result.get("source_reader_attached_during_hmac") is True
        and result.get("source_socket_count_observed") == 1
        and result.get("per_segment_postseal_delivery") is True
        and result.get("postseal_handoff_only") is False
        and result.get("full_continuous_market_hmac_certified") is False
        and result.get("unobserved_venue_history_certified") is False
        and result.get("default_ingress_cutover") is False
        and result.get("institutional_phase1_complete") is False
        and result.get("live_orders_authorized") is False
        and type(result.get("orders_submitted")) is int
        and result["orders_submitted"] == 0
        and result.get("independent_local_audit_passed") is True
        and result.get("receiver_batch_size") == 8
        and result.get("receiver_connection_reuse") is True
        and type(segments) is list and 2 <= len(segments) <= 32
        and type(frames) is int and frames >= frames_per_segment
        and type(originals) is int and originals >= frames + len(segments)
        and type(projected) is int and projected > 0
        and type(result.get("independent_receiver_receipts")) is int
        and result["independent_receiver_receipts"] == projected
        and type(result.get("forward_checkpoints")) is int
        and result["forward_checkpoints"] == projected
        and type(result.get("named_subscriber_receipts")) is int
        and type(result.get("derived_receipts")) is int
        and result["named_subscriber_receipts"] == 2 * result["derived_receipts"]
        and type(result.get("concurrent_segments_completed")) is int
        and 1 <= result["concurrent_segments_completed"] <= len(segments)
        and _bounded(result.get("source_queue_high_watermark"), minimum=1, maximum=63)
        and result.get("adapter_queue_capacity") == 128
        and result.get("segment_queue_capacity") == 2
        and _bounded(result.get("segment_backlog_peak"), minimum=1, maximum=2)
        and _bounded(result.get("outstanding_hmac_segments_peak"), minimum=1, maximum=3)
        and _bounded(result.get("source_original_commit_age_ms_max"), minimum=0, maximum=5000)
        and _bounded(result.get("segment_handoff_lag_max_ms"), minimum=0.001,
                     maximum=max_handoff_age_s * 1000)
        and _bounded(result.get("source_socket_observed_duration_s"),
                     minimum=duration_s - 0.05, maximum=duration_s + 30)
        and proof.get("verdict") == AUDIT_PASS
        and proof.get("orders_submitted") == 0
        and proof.get("HMAC_IPC_channel_independently_reattested") is False
        and proof.get("continuous_live_hmac_verified") is False
        and proof.get("exchange_history_completeness_verified") is False
        and proof.get("all_checkpoint_chain_hashes_verified") is True
        and proof.get("segments") == len(segments)
        and proof.get("raw_original_events") == originals
        and proof.get("source_projections") == projected
        and proof.get("receiver_owned_receipts") == projected
        and proof.get("forward_checkpoints") == projected
        and result.get("independently_audited_reconstructed_projections") == projected
    ):
        raise ValueError("complete source->HMAC->checkpoint independent gate refused")
    return {
        "schema": "econith-p1-public-rotating-hmac-acceptance-v1",
        "verdict": PASS,
        "mode": "SIMULATION",
        "duration_target_s": duration_s,
        "observed_one_socket_duration_s": result["source_socket_observed_duration_s"],
        "one_socket_readers_observed": 1,
        "source_ws_frames": frames,
        "sealed_rotating_segments": len(segments),
        "originals": originals,
        "independently_audited_projections": projected,
        "independent_receiver_receipts": projected,
        "forward_checkpoints": projected,
        "named_subscriber_receipts": result["named_subscriber_receipts"],
        "source_commit_age_ms_max": result["source_original_commit_age_ms_max"],
        "sealed_segment_handoff_lag_ms_max": result["segment_handoff_lag_max_ms"],
        "source_queue_peak": result["source_queue_high_watermark"],
        "sealed_segment_backlog_peak": result["segment_backlog_peak"],
        "receiver_batch_size": 8,
        "max_handoff_age_s": max_handoff_age_s,
        "local_cross_ledger_originals_and_checkpoints_verified": True,
        "hmac_wire_keys_independently_reattested": False,
        "continuous_event_to_hmac_verified": False,
        "exchange_source_completeness_verified": False,
        "institutional_phase1_complete": False,
        "default_ingress_cutover": False,
        "orders_submitted": 0,
    }


async def run_public(root: Path, *, duration_s: float = 120.0,
                     frames_per_segment: int = 450,
                     max_handoff_age_s: float = 30.0) -> dict:
    if (not isinstance(root, Path) or not root.is_absolute()
        or root.exists() or root.is_symlink() or root != root.resolve()
        or root.parent.is_symlink() or not root.parent.is_dir()
        or not _bounded(duration_s, minimum=90, maximum=150)
        or type(frames_per_segment) is not int
        or not 250 <= frames_per_segment <= 650
        or not _bounded(max_handoff_age_s, minimum=1, maximum=30)):
        raise ValueError("unsafe finite public research source scope")
    report = await co_run_rotating_hmac(
        root, duration_s=duration_s,
        max_frames_per_segment=frames_per_segment,
        lease_factory=open_pinned_public_spot_lease,
        clock_preflight=measure_public_clock,
        explicit_public_research_approval=True,
        receiver_batch_size=8, reuse_receiver_connection=True,
        max_handoff_age_s=max_handoff_age_s,
    )
    if report.get("verdict") != SOURCE_PASS:
        raise ValueError("source+receiver co-run did not complete; quarantine")
    proof = independently_inspect_hmac(root, co_run=True)
    return validate_public_proof(
        report, proof, duration_s=duration_s,
        frames_per_segment=frames_per_segment,
        max_handoff_age_s=max_handoff_age_s,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-public-market-data", action="store_true")
    parser.add_argument("--enable-independent-hmac-receiver", action="store_true")
    parser.add_argument("--acknowledge-not-continuous-24h", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=120.0)
    parser.add_argument("--frames-per-segment", type=int, default=450)
    parser.add_argument("--max-handoff-age-s", type=float, default=30.0)
    args = parser.parse_args(argv)
    if not (
        args.allow_public_market_data
        and args.enable_independent_hmac_receiver
        and args.acknowledge_not_continuous_24h
    ):
        parser.error("three explicit public read-only/HMAC scope approvals required")
    try:
        result = asyncio.run(run_public(
            args.output_dir, duration_s=args.duration_s,
            frames_per_segment=args.frames_per_segment,
            max_handoff_age_s=args.max_handoff_age_s,
        ))
    except (ValueError, TypeError, KeyError, IndexError, OSError,
            sqlite3.Error, RuntimeError, TimeoutError) as exc:
        print(json.dumps({
            "verdict": "REFUSED_OR_QUARANTINED",
            "failure_class": type(exc).__name__,
            "orders_submitted": 0,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
