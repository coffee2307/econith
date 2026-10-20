"""P1-10 opt-in risk-first LLM research: stop before four avoidable calls on VETO.

This is a distinct research-only protocol. A risk-first VETO is explicitly
NOT a five-agent advisory and is never serialized as one. When risk allows,
all five bounded role results are reassembled into the original, independently
auditable P1-07 panel schema. No orders, tools, retries or automatic spend.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any, Callable

from ci.phase1_optin_openai_research_panel import (
    SCHEMA as FULL_SCHEMA, _MODEL_RE, _MAX_OUTPUT_TOKENS,
    _MAX_INPUT_TOKENS, _ROLE_TASK, _SYSTEM, _prompt, save_advisory,
)
from ci.phase1_pit_multiagent_advisory import (
    _ROLES, _VOTES, adjudicate_advisory, verified_asof_evidence,
)

GATE_SCHEMA = "econith-p1-risk-first-withheld-no-agent-quorum-v1"
GATE_VERDICT = "WITHHELD_RISK_FIRST_NO_FIVE_AGENT_QUORUM"
_ORDER = ("risk", "technical", "bull", "bear", "portfolio")
assert set(_ORDER) == set(_ROLES)


def _call_role(
    *, client: Any, role: str, model: str, evidence: dict,
    clock_ms: Callable[[], int], first_ms: int, deadline_ms: int,
    peer_stances: dict[str, str],
) -> tuple[dict, dict]:
    if clock_ms() > deadline_ms:
        raise ValueError("risk-first panel deadline passed before model call")
    prompt = _prompt(evidence, role)
    # Only enum stances, never model-authored rationale, enter the
    # portfolio prompt. Avoid untrusted agent-to-agent instruction injection.
    if role == "portfolio":
        prompt += ("\nUntrusted peer stances (NOT additional market facts): " +
                   json.dumps(peer_stances, sort_keys=True, separators=(",", ":")))
    allowed = sorted(_VOTES[role])
    result = client.responses.create(
        model=model, store=False, stream=False, tools=[], tool_choice="none",
        instructions=_SYSTEM, input=prompt,
        text={"format": {
            "type": "json_schema", "name": f"econith_{role}_risk_first_v1",
            "strict": True,
            "schema": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "stance": {"type": "string", "enum": allowed},
                    "rationale": {"type": "string"},
                },
                "required": ["stance", "rationale"],
            },
        }},
        max_output_tokens=_MAX_OUTPUT_TOKENS,
    )
    done = clock_ms()
    if (type(done) is not int or not first_ms <= done <= deadline_ms
        or getattr(result, "status", None) != "completed"
        or type(getattr(result, "output_text", None)) is not str
        or len(result.output_text) > 4096):
        raise ValueError("late, partial, or refused research response")
    answer = json.loads(result.output_text)
    if (type(answer) is not dict or set(answer) != {"stance", "rationale"}
        or type(answer["stance"]) is not str
        or answer["stance"] not in _VOTES[role]
        or type(answer["rationale"]) is not str
        or not 1 <= len(answer["rationale"]) <= 1200
        or any(ord(ch) < 32 and ch not in "\n\t" for ch in answer["rationale"])):
        raise ValueError("invalid structured research analyst response")
    usage = getattr(result, "usage", None)
    it = getattr(usage, "input_tokens", None)
    ot = getattr(usage, "output_tokens", None)
    if (type(it) is not int or not 0 <= it <= _MAX_INPUT_TOKENS
        or type(ot) is not int or not 0 <= ot <= _MAX_OUTPUT_TOKENS):
        raise ValueError("missing/invalid bounded provider token accounting")
    report = {
        "role": role, "stance": answer["stance"],
        "evidence_sha256": evidence["evidence_sha256"],
        "available_at_ms": done, "rationale": answer["rationale"],
    }
    event = {
        "role": role, "model": model,
        "system_sha256": hashlib.sha256(_SYSTEM.encode()).hexdigest(),
        "input_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "output_sha256": hashlib.sha256(result.output_text.encode()).hexdigest(),
        "completed_at_ms": done, "input_tokens": it, "output_tokens": ot,
        "store_requested": False, "tools_enabled": False,
    }
    return report, event


def run_risk_first(
    *, source_root: Path, symbol: str, interval: str, decision_ms: int,
    model: str, client: Any,
    clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
) -> dict:
    """One risk call may veto; otherwise complete five-role quorum in full.

    A refusal/late response can incur partial provider spend and returns NO
    accepted result. Caller must NEVER fall back to bypassing this risk gate.
    """
    if (type(model) is not str or not _MODEL_RE.fullmatch(model)
        or type(decision_ms) is not int or client is None
        or not hasattr(getattr(client, "responses", None), "create")
        or not callable(clock_ms)):
        raise ValueError("invalid model, client or deadline")
    now = clock_ms()
    if type(now) is not int or not 0 < decision_ms - now <= 180_000:
        raise ValueError("decision deadline must be future and within 180s")
    evidence = verified_asof_evidence(
        source_root, symbol=symbol, interval=interval, decision_ms=decision_ms,
    )
    results: dict[str, dict] = {}
    events: dict[str, dict] = {}
    first, event = _call_role(
        client=client, role="risk", model=model, evidence=evidence,
        clock_ms=clock_ms, first_ms=now, deadline_ms=decision_ms,
        peer_stances={},
    )
    results["risk"] = first
    events["risk"] = event
    if first["stance"] == "VETO":
        # No accepted five-role quorum or portfolio rating exists here.
        # Re-open the original private evidence BEFORE returning veto.
        fresh = verified_asof_evidence(
            source_root, symbol=symbol, interval=interval,
            decision_ms=decision_ms,
        )
        if fresh != evidence:
            raise ValueError("risk veto underlying private original changed")
        return {
            "schema": GATE_SCHEMA, "verdict": GATE_VERDICT,
            "model": model, "model_calls": 1,
            "input_tokens": event["input_tokens"],
            "output_tokens": event["output_tokens"],
            "symbol": symbol, "interval": interval,
            "decision_ms": decision_ms,
            "evidence_sha256": evidence["evidence_sha256"],
            "risk_stance": "VETO",
            "risk_rationale_sha256": hashlib.sha256(
                first["rationale"].encode("utf-8")
            ).hexdigest(),
            "agent_quorum_satisfied": False,
            "portfolio_rating": None,
            "advisory_direction": None,
            "historical_backtest_proven": False,
            "realized_pnl_proven": False,
            "model_cost_usd_unverified": True,
            "broker_access": False, "orders_submitted": 0,
        }
    for role in _ORDER[1:]:
        report, event = _call_role(
            client=client, role=role, model=model, evidence=evidence,
            clock_ms=clock_ms, first_ms=now, deadline_ms=decision_ms,
            peer_stances={k: results[k]["stance"]
                          for k in ("risk", "technical", "bull", "bear")
                          if k in results},
        )
        results[role], events[role] = report, event
    ordered_reports = [results[role] for role in _ROLES]
    ordered_events = [events[role] for role in _ROLES]
    adjudicated = adjudicate_advisory(
        evidence, ordered_reports, source_root=source_root,
    )
    return {
        "schema": FULL_SCHEMA,
        "verdict": "PASSED_BOUNDED_LLM_ADVISORY_ONLY",
        "model": model, "evidence": evidence,
        "advisory": adjudicated, "reports": ordered_reports,
        "model_events": ordered_events,
        "model_calls": 5,
        "total_input_tokens": sum(event["input_tokens"] for event in ordered_events),
        "total_output_tokens": sum(event["output_tokens"] for event in ordered_events),
        "model_cost_usd_unverified": True,
        "agent_provider_responses_not_exchange_signed": True,
        "historical_backtest_proven": False, "realized_pnl_proven": False,
        "broker_access": False, "orders_submitted": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-billed-llm-research", action="store_true")
    parser.add_argument("--acknowledge-usage-charges", action="store_true")
    parser.add_argument("--enable-risk-first-protocol", action="store_true")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--interval", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--decision-window-seconds", type=int, default=120)
    args = parser.parse_args(argv)
    if (not args.allow_billed_llm_research
        or not args.acknowledge_usage_charges
        or not args.enable_risk_first_protocol
        or not os.environ.get("OPENAI_API_KEY")
        or not 10 <= args.decision_window_seconds <= 180):
        parser.error("explicit paid calls + experimental risk-first consent and key required")
    from openai import OpenAI  # only after operator's explicit approval
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"],
                    max_retries=0, timeout=20.0)
    try:
        report = run_risk_first(
            source_root=args.source_root, symbol=args.symbol,
            interval=args.interval, model=args.model, client=client,
            decision_ms=time.time_ns() // 1_000_000
                        + args.decision_window_seconds * 1000,
        )
        if report["schema"] == FULL_SCHEMA:
            save_advisory(args.output_dir, report, source_root=args.source_root)
        else:
            # A risk veto produces no finished quorum and is NEVER saved as
            # an accepted llm_advisory.json.
            if (not args.output_dir.is_absolute() or args.output_dir.exists()
                or args.output_dir.is_symlink()
                or args.output_dir != args.output_dir.resolve()
                or not args.output_dir.parent.is_dir()
                or args.output_dir.parent.is_symlink()
                or args.output_dir == args.source_root
                or args.source_root in args.output_dir.parents):
                raise ValueError("unsafe risk refusal output root")
            args.output_dir.mkdir(mode=0o700)
            with (args.output_dir / "risk_gate_refusal.json").open(
                "x", encoding="utf-8"
            ) as f:
                json.dump(report, f, sort_keys=True, indent=2, allow_nan=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
    except (ValueError, TypeError, KeyError, IndexError, OSError,
            json.JSONDecodeError):
        print(json.dumps({
            "verdict": "REFUSED_NO_COMPLETE_RESEARCH",
            "partial_model_spend_possible": True, "orders_submitted": 0,
        }))
        return 2
    print(json.dumps({
        "verdict": report["verdict"],
        "model_calls": report["model_calls"],
        "complete_five_role_quorum": report["schema"] == FULL_SCHEMA,
        "model_cost_usd_measured": False,
        "orders_submitted": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
