"""Static fail-closed guard for ECONITH's isolated verified Spot startup paths.

This inventory does NOT claim the DEFAULT app is verified-only: legacy
BinanceWebSocketStreamer/depth20 and effectful components still boot in the
unchanged default. It records this exact institutional exit blocker and
causes CI failure if an isolated research branch begins instantiating
legacy market publishers, order clients or engine singletons.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

_FORBIDDEN_IN_RESEARCH=frozenset((
    "get_engine","BinanceWebSocketStreamer","CCXTBinanceBridge",
    "get_execution_bridge","QuantExecutionBridge",
    "get_quant_execution_bridge","init_database",
    "get_world_kernel","WorldKernel",
))


class StartupBoundaryAuditError(ValueError):
    pass


def _node_name(call: ast.Call) -> str:
    if isinstance(call.func,ast.Name):
        return call.func.id
    if isinstance(call.func,ast.Attribute):
        return call.func.attr
    return ""


def _policy_test(node: ast.If, choice: str) -> bool:
    t=node.test
    return (
        isinstance(t,ast.Compare)
        and isinstance(t.left,ast.Name)
        and t.left.id=="ingress_policy"
        and len(t.ops)==1 and isinstance(t.ops[0],ast.Eq)
        and len(t.comparators)==1
        and isinstance(t.comparators[0],ast.Constant)
        and t.comparators[0].value==choice
    )


def audit_startup_boundaries(main_source: str, gate_source: str) -> dict:
    try:
        module=ast.parse(main_source)
        policy_module=ast.parse(gate_source)
    except SyntaxError as exc:
        raise StartupBoundaryAuditError("invalid source syntax") from exc
    fn=next((x for x in module.body if isinstance(x,ast.AsyncFunctionDef)
             and x.name=="lifespan"),None)
    if fn is None or len(fn.body)<4:
        raise StartupBoundaryAuditError("lifespan layout not auditable")
    start=fn.body[0]
    if (
        not isinstance(start,ast.Assign)
        or len(start.targets)!=1
        or not isinstance(start.targets[0],ast.Name)
        or start.targets[0].id!="ingress_policy"
        or not isinstance(start.value,ast.Call)
        or _node_name(start.value)!="enforce_verified_only_spot_ingress"
    ):
        raise StartupBoundaryAuditError(
            "verified preflight is not the first executable lifespan statement"
        )
    preview,historical=fn.body[1:3]
    if (
        not isinstance(preview,ast.If)
        or not isinstance(historical,ast.If)
        or not _policy_test(preview,"verified_forward_preview")
        or not _policy_test(historical,"verified_only")
    ):
        raise StartupBoundaryAuditError("protected research policy branches reordered")
    for policy,node in (
        ("verified_forward_preview",preview),("verified_only",historical)
    ):
        if (
            not node.body or not isinstance(node.body[-1],ast.Return)
            or node.body[-1].value is not None
        ):
            raise StartupBoundaryAuditError(
                f"{policy} can fall through to legacy app startup"
            )
        forbidden={
            _node_name(call) for call in ast.walk(node)
            if isinstance(call,ast.Call)
        } & _FORBIDDEN_IN_RESEARCH
        if forbidden:
            raise StartupBoundaryAuditError(
                f"{policy} invokes legacy or execution singleton: {sorted(forbidden)}"
            )
        if not any(
            isinstance(x,ast.Assign)
            and any(isinstance(t,ast.Attribute) and t.attr=="verified_only_research"
                    for t in x.targets)
            for x in ast.walk(node)
        ):
            raise StartupBoundaryAuditError(
                f"{policy} missing protected HTTP surface marker"
            )
    remaining=ast.Module(body=fn.body[3:],type_ignores=[])
    legacy_calls={
        _node_name(x) for x in ast.walk(remaining) if isinstance(x,ast.Call)
    }
    if not ({"get_engine","CCXTBinanceBridge","BinanceWebSocketStreamer"}
            <= legacy_calls):
        raise StartupBoundaryAuditError(
            "legacy app changes require independent manual source review"
        )
    policies=next((x for x in policy_module.body
                   if isinstance(x,ast.Assign)
                   and any(isinstance(t,ast.Name) and t.id=="_SUPPORTED"
                           for t in x.targets)),None)
    if policies is None or not isinstance(policies.value,ast.Call):
        raise StartupBoundaryAuditError("allowed ingress policies unreviewed")
    allowed=next((x for x in ast.walk(policies.value)
                  if isinstance(x,ast.Tuple)),None)
    if allowed is None or {
        x.value for x in allowed.elts if isinstance(x,ast.Constant)
    }!={"legacy","verified_only","verified_forward_preview"}:
        raise StartupBoundaryAuditError("market source policy set unexpectedly changed")
    defaults=[
        x.args[1].value
        for x in ast.walk(policy_module)
        if isinstance(x,ast.Call)
        and isinstance(x.func,ast.Attribute) and x.func.attr=="get"
        and len(x.args)>=2 and isinstance(x.args[0],ast.Constant)
        and x.args[0].value=="ECONITH_SPOT_INGRESS_POLICY"
        and isinstance(x.args[1],ast.Constant)
    ]
    if defaults!=["legacy"]:
        raise StartupBoundaryAuditError("default legacy source changed without review")
    return {
        "phase":"phase1", "analysis":"AST",
        "protected_startup_policy_order":[
            "verified_forward_preview","verified_only","legacy",
        ],
        "research_legacy_execution_singleton_calls":0,
        "legacy_unverified_source_remains_default":True,
        "default_verified_market_data_cutover_certified":False,
        "manual_security_review_still_required":True,
        "status":"PASS_FOR_PROTECTED_BRANCHES_ONLY",
    }


def main() -> int:
    root=Path(__file__).resolve().parents[1]
    try:
        result=audit_startup_boundaries(
            (root/"main.py").read_text(encoding="utf-8"),
            (root/"infrastructure/spot_verified_cutover_gate.py").read_text(
                encoding="utf-8",
            ),
        )
    except StartupBoundaryAuditError as exc:
        print(json.dumps({"status":"FAIL_CLOSED","reason":str(exc)}))
        return 1
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
