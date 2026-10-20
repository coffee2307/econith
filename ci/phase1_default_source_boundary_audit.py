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



def _audit_research_http_websocket_surface(module: ast.Module)->None:
    """No opt-in research branch may expose existing legacy route handlers.

    This is a structural regression check, NOT a production penetration test.
    Actual ASGI route blacklisting has its own independent request tests.
    """
    middleware=next(
        (fn for fn in module.body
         if isinstance(fn,ast.AsyncFunctionDef)
         and fn.name=="_verified_only_research_surface"),
        None,
    )
    if middleware is None:
        raise StartupBoundaryAuditError("protected research HTTP middleware missing")
    recognized=any(
        isinstance(d,ast.Call) and _node_name(d)=="middleware"
        and len(d.args)==1 and isinstance(d.args[0],ast.Constant)
        and d.args[0].value=="http"
        for d in middleware.decorator_list
    )
    if not recognized:
        raise StartupBoundaryAuditError("research HTTP middleware not registered")
    if len(middleware.body)<2 or not isinstance(middleware.body[0],ast.If):
        raise StartupBoundaryAuditError("research HTTP route guard moved or removed")
    guard=middleware.body[0]
    if not any(
        isinstance(call,ast.Call) and _node_name(call)=="getattr"
        and len(call.args)>=2 and isinstance(call.args[1],ast.Constant)
        and call.args[1].value=="verified_only_research"
        for call in ast.walk(guard.test)
    ):
        raise StartupBoundaryAuditError("research HTTP guard not bound to protected app state")
    if not guard.body or not isinstance(guard.body[0],ast.Assign):
        raise StartupBoundaryAuditError("research HTTP permitted status route missing")
    expr=guard.body[0].value
    if not (isinstance(expr,ast.JoinedStr)
            and any(isinstance(x,ast.Constant)
                    and x.value=="/market/verified/status"
                    for x in expr.values)):
        raise StartupBoundaryAuditError("research HTTP allowed endpoint expanded")
    route_if=next((x for x in guard.body if isinstance(x,ast.If)),None)
    if route_if is None or not isinstance(route_if.test,ast.BoolOp) or not isinstance(route_if.test.op,ast.Or):
        raise StartupBoundaryAuditError("research HTTP must refuse wrong path OR method")
    compares=[x for x in route_if.test.values if isinstance(x,ast.Compare)]
    if len(compares)!=2:
        raise StartupBoundaryAuditError("research HTTP route denial comparisons changed")
    path_gate=any(
        isinstance(x.left,ast.Attribute) and x.left.attr=="path"
        and len(x.ops)==1 and isinstance(x.ops[0],ast.NotEq)
        and len(x.comparators)==1 and isinstance(x.comparators[0],ast.Name)
        and x.comparators[0].id=="permitted"
        for x in compares
    )
    method_gate=any(
        isinstance(x.left,ast.Attribute) and x.left.attr=="method"
        and len(x.ops)==1 and isinstance(x.ops[0],ast.NotEq)
        and len(x.comparators)==1
        and isinstance(x.comparators[0],ast.Constant)
        and x.comparators[0].value=="GET"
        for x in compares
    )
    if not path_gate or not method_gate:
        raise StartupBoundaryAuditError("research HTTP path/method fail-closed check removed")
    response=route_if.body[0] if route_if.body else None
    if (not isinstance(response,ast.Return)
        or not isinstance(response.value,ast.Call)
        or _node_name(response.value)!="JSONResponse"
        or not any(k.arg=="status_code"
                   and isinstance(k.value,ast.Constant)
                   and k.value.value==404
                   for k in response.value.keywords)):
        raise StartupBoundaryAuditError("research HTTP refusal must remain 404")
    if (len(middleware.body)<2 or not isinstance(middleware.body[-1],ast.Return)
        or not isinstance(middleware.body[-1].value,ast.Await)
        or not isinstance(middleware.body[-1].value.value,ast.Call)
        or _node_name(middleware.body[-1].value.value)!="call_next"):
        raise StartupBoundaryAuditError("research HTTP middleware downstream structure altered")

    ws=next((fn for fn in module.body
             if isinstance(fn,ast.AsyncFunctionDef)
             and fn.name=="stream_metrics"),None)
    if ws is None or not ws.body or not isinstance(ws.body[0],ast.If):
        raise StartupBoundaryAuditError("protected research metrics WS guard missing")
    ws_guard=ws.body[0]
    if not any(
        isinstance(call,ast.Call) and _node_name(call)=="getattr"
        and len(call.args)>=2 and isinstance(call.args[1],ast.Constant)
        and call.args[1].value=="verified_only_research"
        for call in ast.walk(ws_guard.test)
    ):
        raise StartupBoundaryAuditError("protected research WS state guard removed")
    if (len(ws_guard.body)<2
        or not isinstance(ws_guard.body[0],ast.Expr)
        or not isinstance(ws_guard.body[0].value,ast.Await)
        or not isinstance(ws_guard.body[0].value.value,ast.Call)
        or _node_name(ws_guard.body[0].value.value)!="close"
        or not any(
            k.arg=="code" and isinstance(k.value,ast.Constant)
            and k.value.value==1008
            for k in ws_guard.body[0].value.value.keywords
        )
        or not isinstance(ws_guard.body[1],ast.Return)):
        raise StartupBoundaryAuditError("protected research metrics WS must close 1008 before accept")


def audit_startup_boundaries(main_source: str, gate_source: str) -> dict:
    try:
        module=ast.parse(main_source)
        policy_module=ast.parse(gate_source)
    except SyntaxError as exc:
        raise StartupBoundaryAuditError("invalid source syntax") from exc
    _audit_research_http_websocket_surface(module)
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
        "research_http_route_allowlist_covered_by_ast":True,
        "research_legacy_metrics_websocket_close_guarded_by_ast":True,
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
