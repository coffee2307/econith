"""Bounded, aggregate-only in-session source-to-publication admission-age report.

Diagnostic file is NOT an immutable market-source receipt and never certifies
continuous coverage. No event payload, source identifier or absolute clock is
recorded. Histograms use fixed 50ms buckets and constant storage.
"""
from __future__ import annotations

import json
from math import ceil
from pathlib import Path

_STEP_MS = 50
_MAX_MS = 10_000
_BIN_COUNT = _MAX_MS // _STEP_MS + 2


class BoundedProjectorLag:
    def __init__(self, file: Path, *, age_budget_ms: int):
        if (not isinstance(file, Path) or file.name != "projector_lag.json"
            or not file.parent.is_dir() or file.exists() or file.is_symlink()
            or type(age_budget_ms) is not int or age_budget_ms <= 0
            or age_budget_ms > _MAX_MS):
            raise ValueError("invalid fresh projector lag diagnostic")
        self.file = file
        self.budget = age_budget_ms
        self._age = [0] * _BIN_COUNT
        self._pending = [0] * 65  # 0..63, 64+; exact peak tracked separately
        self._count = 0
        self._max = 0
        self._pending_count = 0
        self._max_pending = 0

    def admit(self, age_ms: int) -> None:
        """Call only AFTER successful verified EventBus publication.

        Measured age is the source timestamp to the admission gate directly
        BEFORE publishing, not end-of-EventBus/observer completion time.
        """
        if type(age_ms) is not int or age_ms < 0:
            raise ValueError("invalid source admission age")
        index = min(age_ms // _STEP_MS, _BIN_COUNT - 1)
        self._age[index] += 1
        self._count += 1
        self._max = max(self._max, age_ms)

    def pending(self, count: int) -> None:
        if type(count) is not int or count < 0:
            raise ValueError("invalid pending durable originals")
        self._pending[min(count, 64)] += 1
        self._pending_count += 1
        self._max_pending = max(self._max_pending, count)

    def _percentile_upper_bound(self, fraction: float) -> int | None:
        if not self._count:
            return None
        target = ceil(self._count * fraction)
        tally = 0
        for index, n in enumerate(self._age):
            tally += n
            if tally >= target:
                return min((index + 1) * _STEP_MS - 1, _MAX_MS + _STEP_MS)
        raise RuntimeError("projector age histogram inconsistent")

    def summary(self, *, source_complete: bool) -> dict:
        return {
            "schema": "econith-phase1-projector-age-histogram-v1",
            "diagnostic_only_not_source_evidence": True,
            "source_complete": bool(source_complete),
            "admission_age_definition": "source_received_at_to_pre_EventBus_admission",
            "age_bucket_width_ms": _STEP_MS,
            "age_samples": self._count,
            "p50_age_upper_ms": self._percentile_upper_bound(.50),
            "p95_age_upper_ms": self._percentile_upper_bound(.95),
            "p99_age_upper_ms": self._percentile_upper_bound(.99),
            "max_age_exact_ms": self._max if self._count else None,
            "freshness_budget_ms": self.budget,
            "minimum_observed_headroom_ms": self.budget - self._max if self._count else None,
            "age_buckets": list(self._age),
            "pending_observations": self._pending_count,
            "max_pending_originals": self._max_pending,
            "pending_buckets_64plus": list(self._pending),
            "raw_events_or_identifiers_recorded": False,
        }

    def write(self, *, source_complete: bool) -> None:
        # A fresh attempt-specific diagnostics file, never the source ledger.
        with self.file.open("x", encoding="utf-8") as out:
            out.write(
                json.dumps(self.summary(source_complete=source_complete),
                           sort_keys=True, indent=2, allow_nan=False) + "\n"
            )
