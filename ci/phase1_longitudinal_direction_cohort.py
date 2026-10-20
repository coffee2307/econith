"""P1-09: bounded source-recomputed multi-decision research quality cohort.

All inputs are prior, finite two-vintage scorecard bundles. Each is audited
against both local original source archives before its direction outcome can
enter the cohort. Scores concern direction only, not fills or returns.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ci.phase1_research_evidence_bridge import audit as audit_research
from ci.phase1_pit_decision_scorecard import SCHEMA as SCORE_SCHEMA

SCHEMA = "econith-p1-longitudinal-direction-cohort-v1"
VERDICT = "PASSED_REAUDITED_FINITE_DIRECTION_COHORT_ONLY"
MAX_ENTRIES = 96
MAX_MANIFEST_BYTES = 64 * 1024
MAX_SCORECARD_BYTES = 512 * 1024
_ENTRY_KEYS = frozenset(("report_dir", "decision_root", "followup_root"))


def _read_limited(path: Path, bound: int) -> bytes:
    if (not isinstance(path, Path) or not path.is_absolute()
        or path.is_symlink() or not path.is_file()
        or path != path.resolve() or not 0 < path.stat().st_size <= bound):
        raise ValueError("untrusted, missing, or oversized research input")
    raw = path.read_bytes()
    if not 0 < len(raw) <= bound:
        raise ValueError("input changed during bounded read")
    return raw


def _manifest(path: Path) -> tuple[list[dict[str, Path]], str]:
    raw = _read_limited(path, MAX_MANIFEST_BYTES)
    doc = json.loads(raw)
    if (type(doc) is not dict or set(doc) != {"entries"}
        or type(doc["entries"]) is not list
        or not 1 <= len(doc["entries"]) <= MAX_ENTRIES):
        raise ValueError("explicit bounded scorecard manifest required")
    parsed = []
    for row in doc["entries"]:
        if (type(row) is not dict or set(row) != _ENTRY_KEYS
            or any(type(v) is not str or len(v) > 4096 for v in row.values())):
            raise ValueError("only explicit absolute source and report roots supported")
        entry = {k: Path(row[k]) for k in _ENTRY_KEYS}
        if any(not v.is_absolute() or v != v.resolve() for v in entry.values()):
            raise ValueError("relative or aliased cohort path")
        if len(set(entry.values())) != 3:
            raise ValueError("decision, future and report roots must be distinct")
        parsed.append(entry)
    return parsed, hashlib.sha256(raw).hexdigest()


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 6) if d else None


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    ai_calls = [r for r in rows if r["ai_correct"] is not None]
    baseline_calls = [r for r in rows if r["baseline_correct"] is not None]
    paired = [r for r in rows if (
        r["ai_correct"] is not None and r["baseline_correct"] is not None
    )]
    ai_right = sum(r["ai_correct"] is True for r in ai_calls)
    baseline_right = sum(r["baseline_correct"] is True for r in baseline_calls)
    pair_ai = sum(r["ai_correct"] is True for r in paired)
    pair_base = sum(r["baseline_correct"] is True for r in paired)
    return {
        "decisions": total,
        "risk_veto": sum(r["risk_veto"] for r in rows),
        "ai_abstained_or_unscorable": total - len(ai_calls),
        "baseline_abstained_or_unscorable": total - len(baseline_calls),
        "ai_scorable": len(ai_calls),
        "ai_correct": ai_right,
        "ai_accuracy_conditional": _ratio(ai_right, len(ai_calls)),
        "ai_scorable_fraction": _ratio(len(ai_calls), total),
        "baseline_scorable": len(baseline_calls),
        "baseline_correct": baseline_right,
        "baseline_accuracy_conditional": _ratio(baseline_right, len(baseline_calls)),
        "paired_scorable": len(paired),
        "paired_ai_correct": pair_ai,
        "paired_baseline_correct": pair_base,
        "paired_ai_accuracy": _ratio(pair_ai, len(paired)),
        "paired_baseline_accuracy": _ratio(pair_base, len(paired)),
        "paired_difference_decimal": _ratio(pair_ai - pair_base, len(paired)),
        "paired_ai_only_right": sum(
            r["ai_correct"] is True and r["baseline_correct"] is False
            for r in paired
        ),
        "paired_baseline_only_right": sum(
            r["ai_correct"] is False and r["baseline_correct"] is True
            for r in paired
        ),
        "paired_same_result": sum(
            r["ai_correct"] is r["baseline_correct"] for r in paired
        ),
    }


def evaluate_manifest(manifest_path: Path) -> dict:
    """Audit every row again from private originals; reject partial cohorts.

    No successful output is produced if any selected row fails. The manifest
    is caller-selected: completeness and unbiased sampling are NOT proven.
    """
    entries, manifest_sha = _manifest(manifest_path)
    results = []
    unique_decisions: set[tuple[str, str, int]] = set()
    unique_outcomes: set[tuple[str, str, int]] = set()
    unique_reports: set[str] = set()
    for entry in entries:
        rec = audit_research(
            kind="scorecard", report_dir=entry["report_dir"],
            decision_root=entry["decision_root"],
            followup_root=entry["followup_root"],
        )
        path = entry["report_dir"] / "finite_scorecard.json"
        raw = _read_limited(path, MAX_SCORECARD_BYTES)
        if hashlib.sha256(raw).hexdigest() != rec["report_sha256"]:
            raise ValueError("scorecard changed since independent re-audit")
        payload = json.loads(raw)
        if (payload.get("schema") != SCORE_SCHEMA
            or type(payload.get("score")) is not dict):
            raise ValueError("invalid scorecard cohort member")
        s = payload["score"]
        if s.get("score_sha256") != rec["evidence_sha256"]:
            raise ValueError("scorecard changed before cohort aggregation")
        key = (s["symbol"], s["interval"], s["decision_ms"])
        future_key = (s["symbol"], s["interval"], s["first_forward_candle_end_ms"])
        if (key in unique_decisions or future_key in unique_outcomes
            or rec["report_sha256"] in unique_reports):
            raise ValueError("duplicate decision, outcome candle or exact report")
        unique_decisions.add(key)
        unique_outcomes.add(future_key)
        unique_reports.add(rec["report_sha256"])
        if (s["decision_ms"] >= s["first_forward_candle_end_ms"]
            or s["decision_source_observed_after_ms"] > s["decision_ms"]
            or s["followup_source_observed_after_ms"] <= s["decision_ms"]):
            raise ValueError("chronological source authority violated")
        row = {
            "symbol": s["symbol"], "interval": s["interval"],
            "decision_ms": s["decision_ms"],
            "first_forward_candle_end_ms": s["first_forward_candle_end_ms"],
            "ai_direction": s["advisory_direction"],
            "baseline_direction": s["prior_closed_bar_momentum_direction"],
            "ai_correct": s["advisory_direction_correct"],
            "baseline_correct": s["prior_closed_bar_direction_correct"],
            "risk_veto": s["advisory_verdict"] == "WITHHELD_RISK_VETO",
            "report_sha256": rec["report_sha256"],
            "score_sha256": s["score_sha256"],
        }
        results.append(row)
    results.sort(key=lambda r: (r["decision_ms"], r["symbol"], r["interval"]))
    splits = {}
    for symbol in ("BTCUSDT", "ETHUSDT", "BNBUSDT"):
        for interval in ("1m", "5m"):
            subset = [r for r in results if r["symbol"] == symbol
                      and r["interval"] == interval]
            if subset:
                splits[f"{symbol}:{interval}"] = _summary(subset)
    return {
        "schema": SCHEMA,
        "verdict": VERDICT,
        "manifest_sha256": manifest_sha,
        "total_original_reaudits": len(results),
        "scope": "caller-selected-finite-originals-only",
        "data_selection_complete_verified": False,
        "independent_samples_certified": False,
        "statistical_significance_claimed": False,
        "realized_pnl_computed": False,
        "broker_access": False,
        "orders_submitted": 0,
        "source_receipts_signed": False,
        "summary": _summary(results),
        "by_market": splits,
        "decisions": results,
    }


def write_cohort(output_dir: Path, record: dict) -> None:
    if (not isinstance(output_dir, Path) or not output_dir.is_absolute()
        or output_dir.exists() or output_dir.is_symlink()
        or output_dir != output_dir.resolve()
        or not output_dir.parent.is_dir() or output_dir.parent.is_symlink()
        or record.get("schema") != SCHEMA or record.get("verdict") != VERDICT):
        raise ValueError("new isolated cohort destination required")
    output_dir.mkdir(mode=0o700)
    path = output_dir / "research_quality_cohort.json"
    with path.open("x", encoding="utf-8") as out:
        json.dump(record, out, sort_keys=True, indent=2, allow_nan=False)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-offline-research-cohort", action="store_true")
    p.add_argument("--acknowledge-no-executed-pnl", action="store_true")
    p.add_argument("--manifest", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    a = p.parse_args(argv)
    if not a.allow_offline_research_cohort or not a.acknowledge_no_executed_pnl:
        p.error("explicit opt-in and non-PnL acknowledgement required")
    try:
        entries, _ = _manifest(a.manifest)
        private_roots = [path for e in entries for path in e.values()]
        private_roots.append(a.manifest.parent)
        if any(a.output_dir == root or root in a.output_dir.parents
               for root in private_roots):
            raise ValueError("cohort output must be outside input and original roots")
        cohort = evaluate_manifest(a.manifest)
        write_cohort(a.output_dir, cohort)
    except (ValueError, TypeError, KeyError, IndexError, OSError,
            json.JSONDecodeError):
        print(json.dumps({"verdict": "REFUSED", "orders_submitted": 0}))
        return 2
    print(json.dumps({
        "verdict": VERDICT, "decisions": cohort["summary"]["decisions"],
        "paired_scorable": cohort["summary"]["paired_scorable"],
        "orders_submitted": 0, "realized_pnl_computed": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
