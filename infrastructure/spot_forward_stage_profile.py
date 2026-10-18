"""Opt-in bounded, aggregate-only timing for Phase 1 forward research.

Not a source/venue certificate. NEVER contains raw frames, URLs, machine paths,
private tokens, original identifiers, event timestamps or per-event traces.
Diagnostic writes are separate from immutable market source/ACK ledgers.
"""
from __future__ import annotations

import json
import math
import os
import time
from contextlib import contextmanager
from pathlib import Path

_ALLOWED = frozenset({
    "source_sqlite_commit", "source_verify", "shadow_verify",
    "outbox_stage", "receiver_hmac_ack", "outbox_receiver_ack",
    "eventbus_publish", "derived_subscriber_wait",
    "outbox_derived_ack", "checkpoint_commit",
})
_MAX_SAMPLES = 4096


class BoundedStageProfiler:
    def __init__(self, path: Path):
        if path.name != "stage_latency.json" or not path.parent.is_dir():
            raise ValueError("diagnostic output must be a new session-local file")
        self.path = path
        self.samples: dict[str, list[float]] = {}

    @contextmanager
    def measure(self, stage: str):
        if stage not in _ALLOWED:
            raise ValueError("unrecognized diagnostic stage")
        started = time.perf_counter_ns()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            values = self.samples.setdefault(stage, [])
            if len(values) < _MAX_SAMPLES and math.isfinite(elapsed_ms):
                values.append(round(elapsed_ms, 5))

    @staticmethod
    def _percentile(sorted_values: list[float], fraction: float) -> float:
        return sorted_values[math.ceil(fraction * len(sorted_values)) - 1]

    def summary(self, *, completed: bool) -> dict:
        stages = {}
        for name, values in sorted(self.samples.items()):
            if not values:
                continue
            ordered = sorted(values)
            stages[name] = {
                "samples": len(values),
                "max_samples": _MAX_SAMPLES,
                "sample_limit_reached": len(values) == _MAX_SAMPLES,
                "total_ms": round(sum(values), 3),
                "mean_ms": round(sum(values) / len(values), 3),
                "p50_ms": round(self._percentile(ordered, 0.50), 3),
                "p95_ms": round(self._percentile(ordered, 0.95), 3),
                "p99_ms": round(self._percentile(ordered, 0.99), 3),
                "max_ms": round(ordered[-1], 3),
            }
        return {
            "schema": "econith-phase1-stage-latency-v1",
            "diagnostic_not_source_evidence": True,
            "source_complete": bool(completed),
            "raw_event_contents_recorded": False,
            "samples_truncated_at": _MAX_SAMPLES,
            "stages": stages,
        }

    def write(self, *, completed: bool) -> None:
        # New session directory is never reused. Diagnostic file cannot be
        # overwritten; no change to original, outbox, receiver or checkpoint.
        self.path.write_text(
            json.dumps(self.summary(completed=completed),
                       indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
