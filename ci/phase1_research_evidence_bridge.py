"""Offline, read-only source re-audit bridge for the ECONITH research viewer.

Audits the original private Spot archives using the existing Python auditors,
then emits a *sanitized, unsigned* local receipt bound to the exact report
bytes. A browser can match a receipt to a report but MUST NOT treat an
attacker-supplied receipt as independently authenticated. No HTTP, LLM,
execution, or private original export.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from ci.phase1_optin_openai_research_panel import SCHEMA as PANEL_SCHEMA
from ci.phase1_pit_decision_scorecard import (
    SCHEMA as SCORE_SCHEMA, independently_reaudit_scorecard,
)
from ci.phase1_pit_multiagent_advisory import (
    _ROLES, adjudicate_advisory,
)

SCHEMA = "econith-p1-local-research-audit-receipt-v1"
VERDICT = "PASSED_LOCAL_PYTHON_ORIGINAL_REAUDIT_ONLY"
AUDIT_MODE = "offline-original-source-recomputation"
MAX_REPORT_BYTES = 512 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PANEL_KEYS = {
    "schema", "verdict", "model", "evidence", "advisory", "reports",
    "model_events", "model_calls", "total_input_tokens", "total_output_tokens",
    "model_cost_usd_unverified", "agent_provider_responses_not_exchange_signed",
    "historical_backtest_proven", "realized_pnl_proven", "broker_access",
    "orders_submitted",
}


def _safe_dir(root: Path) -> bool:
    return (
        isinstance(root, Path) and root.is_absolute() and root.is_dir()
        and not root.is_symlink() and root == root.resolve()
    )


def _load_report(root: Path, name: str) -> tuple[dict, bytes]:
    if not _safe_dir(root):
        raise ValueError("unsafe report directory")
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or aliased report")
    size = path.stat().st_size
    if size <= 0 or size > MAX_REPORT_BYTES:
        raise ValueError("report size outside bounded research contract")
    raw = path.read_bytes()
    if len(raw) != size:
        raise ValueError("report changed during read")
    payload = json.loads(raw)
    if type(payload) is not dict:
        raise ValueError("invalid research report object")
    return payload, raw


def _audit_panel(payload: dict, source_root: Path) -> dict:
    if (
        set(payload) != _PANEL_KEYS
        or payload["schema"] != PANEL_SCHEMA
        or payload["verdict"] != "PASSED_BOUNDED_LLM_ADVISORY_ONLY"
        or type(payload["model"]) is not str
        or not 2 <= len(payload["model"]) <= 96
        or type(payload["model_calls"]) is not int
        or payload["model_calls"] != len(_ROLES)
        or type(payload["orders_submitted"]) is not int
        or payload["orders_submitted"] != 0
        or payload["broker_access"] is not False
        or payload["model_cost_usd_unverified"] is not True
        or payload["agent_provider_responses_not_exchange_signed"] is not True
        or payload["historical_backtest_proven"] is not False
        or payload["realized_pnl_proven"] is not False
    ):
        raise ValueError("unsafe panel contract")
    evidence = payload["evidence"]
    reports = payload["reports"]
    advisory = payload["advisory"]
    computed = adjudicate_advisory(evidence, reports, source_root=source_root)
    if computed != advisory:
        raise ValueError("recomputed source-bound panel verdict differs")
    events = payload["model_events"]
    if type(events) is not list or len(events) != 5:
        raise ValueError("incomplete model event list")
    ins, outs = 0, 0
    for role, report, event in zip(_ROLES, reports, events):
        if (
            type(event) is not dict
            or set(event) != {
                "role", "model", "system_sha256", "input_sha256",
                "output_sha256", "completed_at_ms", "input_tokens",
                "output_tokens", "store_requested", "tools_enabled",
            }
            or event["role"] != role or report["role"] != role
            or event["model"] != payload["model"]
            or event["completed_at_ms"] != report["available_at_ms"]
            or event["store_requested"] is not False
            or event["tools_enabled"] is not False
            or any(
                type(event[key]) is not str or not _SHA256.fullmatch(event[key])
                for key in ("system_sha256", "input_sha256", "output_sha256")
            )
            or type(event["input_tokens"]) is not int
            or not 0 <= event["input_tokens"] <= 4096
            or type(event["output_tokens"]) is not int
            or not 0 <= event["output_tokens"] <= 240
        ):
            raise ValueError("unsupported or altered model event")
        ins += event["input_tokens"]
        outs += event["output_tokens"]
    if (
        type(payload["total_input_tokens"]) is not int
        or type(payload["total_output_tokens"]) is not int
        or ins != payload["total_input_tokens"]
        or outs != payload["total_output_tokens"]
    ):
        raise ValueError("model token accounting disagrees")
    return {
        "symbol": evidence["symbol"],
        "interval": evidence["interval"],
        "decision_ms": evidence["decision_ms"],
        "evidence_sha256": evidence["evidence_sha256"],
        "risk_veto": advisory["risk_veto"],
    }


def _audit_scorecard(report_dir: Path, payload: dict, decision_root: Path,
                     followup_root: Path) -> dict:
    if (
        payload.get("schema") != SCORE_SCHEMA
        or type(payload.get("score")) is not dict
        or payload["score"].get("out_of_sample_profitability_proven") is not False
        or payload["score"].get("net_profit_after_costs_computed") is not False
    ):
        raise ValueError("incomplete non-PnL scorecard")
    recomputed = independently_reaudit_scorecard(
        report_dir, decision_root, followup_root,
    )
    if (
        recomputed["verdict"]
        != "PASSED_READONLY_ORIGINALS_AND_DECISION_RECOMPUTATION_ONLY"
        or recomputed["orders_submitted"] != 0
    ):
        raise ValueError("source re-audit did not pass")
    score = payload["score"]
    if recomputed["score_sha256"] != score["score_sha256"]:
        raise ValueError("scorecard digest disagrees")
    return {
        "symbol": score["symbol"],
        "interval": score["interval"],
        "decision_ms": score["decision_ms"],
        "evidence_sha256": score["score_sha256"],
        "risk_veto": score["advisory_verdict"] == "WITHHELD_RISK_VETO",
    }


def audit(*, kind: str, report_dir: Path, decision_root: Path,
          followup_root: Path | None = None) -> dict:
    """Recompute from originals and bind the result to exact saved JSON bytes."""
    if kind not in ("panel", "scorecard") or not _safe_dir(decision_root):
        raise ValueError("invalid kind or decision originals")
    if kind == "scorecard" and not _safe_dir(followup_root):
        raise ValueError("independent follow-up originals required")
    if kind == "panel" and followup_root is not None:
        raise ValueError("panel does not use a future archive")
    if report_dir == decision_root or (
        followup_root is not None
        and (report_dir == followup_root or decision_root == followup_root)
    ):
        raise ValueError("source and report roots must be separated")
    name = "llm_advisory.json" if kind == "panel" else "finite_scorecard.json"
    payload, raw = _load_report(report_dir, name)
    if kind == "panel":
        details = _audit_panel(payload, decision_root)
    else:
        details = _audit_scorecard(report_dir, payload, decision_root, followup_root)
    # Refuse a report changed while the underlying original-source audit ran.
    if (report_dir / name).read_bytes() != raw:
        raise ValueError("report changed during original-source re-audit")
    return {
        "schema": SCHEMA,
        "verdict": VERDICT,
        "audit_mode": AUDIT_MODE,
        "kind": kind,
        "report_sha256": hashlib.sha256(raw).hexdigest(),
        **details,
        "signed_by_trusted_authority": False,
        "exchange_origin_verified": False,
        "real_trading_results_proven": False,
        "orders_submitted": 0,
    }


def export_receipt(output_dir: Path, receipt: dict) -> None:
    """Create exactly one fresh local receipt, never overwrite existing proofs."""
    if (
        not isinstance(output_dir, Path)
        or not output_dir.is_absolute()
        or output_dir.exists() or output_dir.is_symlink()
        or output_dir != output_dir.resolve()
        or not output_dir.parent.is_dir() or output_dir.parent.is_symlink()
        or receipt.get("schema") != SCHEMA
        or receipt.get("verdict") != VERDICT
    ):
        raise ValueError("fresh receipt directory required")
    output_dir.mkdir(mode=0o700)
    with (output_dir / "research_audit_receipt.json").open(
        "x", encoding="utf-8"
    ) as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--allow-offline-original-audit", action="store_true")
    p.add_argument("--kind", choices=("panel", "scorecard"), required=True)
    p.add_argument("--report-dir", type=Path, required=True)
    p.add_argument("--decision-root", type=Path, required=True)
    p.add_argument("--followup-root", type=Path)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args(argv)
    if not args.allow_offline_original_audit:
        p.error("explicit read-only original-data audit acknowledgement required")
    try:
        roots = (args.report_dir, args.decision_root, args.followup_root)
        if any(root is not None and (
            args.output_dir == root or root in args.output_dir.parents
        ) for root in roots):
            raise ValueError("receipt destination cannot be inside private evidence roots")
        receipt = audit(
            kind=args.kind, report_dir=args.report_dir,
            decision_root=args.decision_root,
            followup_root=args.followup_root,
        )
        export_receipt(args.output_dir, receipt)
    except (ValueError, TypeError, KeyError, IndexError, OSError,
            json.JSONDecodeError):
        print(json.dumps({"verdict": "REFUSED", "orders_submitted": 0}))
        return 2
    print(json.dumps({
        "verdict": VERDICT, "kind": args.kind, "orders_submitted": 0,
        "receipt_sha256": hashlib.sha256(
            (args.output_dir / "research_audit_receipt.json").read_bytes()
        ).hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
