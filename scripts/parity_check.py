"""ECONITH :: scripts.parity_check

Post-cutover parity + clean-cut verification for the native kernelization.

Proves:
1) ROUTE PARITY   — EconithRouteKernel produces identical allocations to the
   reference NoFx split formula across randomized inputs (0% logic drift).
2) FRICTION SANITY — EconithFrictionModel returns positive, finite friction and
   a monotonic cost in quantity.
3) CONSENSUS/ALPHA — native kernels import and produce bounded, well-formed output.
4) CLEAN CUT      — runtime logic modules do NOT import bridges.vendor_shims.
"""
from __future__ import annotations

import ast
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from econith.quant.alpha import EconithAlphaKernel  # noqa: E402
from econith.quant.backtest.friction import EconithFrictionModel  # noqa: E402
from econith.quant.consensus import EconithConsensusKernel  # noqa: E402
from econith.quant.routing import EconithRouteKernel  # noqa: E402
from econith.quant.routing.models import PROFILES  # noqa: E402


class _Res:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def ok(self, name: str) -> None:
        print(f"  [PASS] {name}")

    def fail(self, name: str, why: str) -> None:
        self.failed.append(name)
        print(f"  [FAIL] {name} -> {why}")


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _reference_split(profile_name: str, direction: float, confidence: float,
                     base_quantity: float, symbol: str | None) -> list[tuple[str, float, float]]:
    """Frozen reference of the original NoFx-style allocation formula."""
    p = PROFILES[profile_name]
    conf = _clamp(confidence, 0.0, 1.0)
    dirn = _clamp(direction * p.bias_multiplier, -1.0, 1.0)
    universe = (symbol.upper(),) if symbol else p.symbols
    if not universe or base_quantity <= 0 or abs(dirn) < 1e-9:
        return []
    n = len(universe)
    per_weight = min(p.max_leg_fraction, 1.0 / n)
    scale = abs(dirn) * conf
    out: list[tuple[str, float, float]] = []
    for sym in universe:
        qty = base_quantity * scale * per_weight
        if qty > 0:
            out.append((sym, qty, per_weight))
    return out


def test_route_parity(res: _Res) -> None:
    print("\n[1] ROUTE PARITY — EconithRouteKernel == reference NoFx split")
    rng = random.Random(42)
    mismatches = 0
    for _ in range(2000):
        profile = rng.choice(list(PROFILES.keys()))
        direction = rng.uniform(-1.0, 1.0)
        confidence = rng.uniform(0.0, 1.0)
        base_qty = rng.uniform(0.0, 5.0)
        symbol = rng.choice([None, "BTCUSDT", "ETHUSDT"])

        kernel = EconithRouteKernel(profile)
        plan = kernel.build_plan(
            direction=direction, confidence=confidence,
            base_quantity=base_qty, reduce_only=False, symbol=symbol,
        )
        ref = _reference_split(profile, direction, confidence, base_qty, symbol)
        got = [(leg.symbol, leg.quantity, leg.weight) for leg in plan.legs]
        if len(got) != len(ref):
            mismatches += 1
            continue
        for (gs, gq, gw), (rs, rq, rw) in zip(got, ref):
            if gs != rs or abs(gq - rq) > 1e-12 or abs(gw - rw) > 1e-12:
                mismatches += 1
                break
    if mismatches == 0:
        res.ok("route_parity_0pct_drift (2000 cases)")
    else:
        res.fail("route_parity_0pct_drift", f"{mismatches} mismatches")


def test_friction(res: _Res) -> None:
    print("\n[2] FRICTION SANITY — EconithFrictionModel")
    m = EconithFrictionModel()
    bps = m.aggregate_friction_bps()
    q1 = m.friction_quote({"price": 100.0, "quantity": 1.0, "side": "BUY"}, {"adv": 1000.0})
    q2 = m.friction_quote({"price": 100.0, "quantity": 10.0, "side": "BUY"}, {"adv": 1000.0})
    if bps > 0 and q1.total_cost >= 0 and q2.total_cost >= q1.total_cost:
        res.ok("friction_positive_and_monotonic")
    else:
        res.fail("friction_positive_and_monotonic", f"bps={bps} c1={q1.total_cost} c2={q2.total_cost}")


def test_consensus_alpha(res: _Res) -> None:
    print("\n[3] CONSENSUS + ALPHA — native kernels bounded output")
    ctx = {"obi": 0.4, "yield_spread_10y_2y": 0.01, "realized_vol": 0.003, "funding_rate": 0.0002}
    verdict = EconithConsensusKernel().deliberate(ctx)
    cand = EconithAlphaKernel().predict(ctx, "TRENDING")
    ok = (
        -1.0 <= verdict.bias <= 1.0
        and 0.0 <= verdict.confidence <= 1.0
        and verdict.has_signal
        and cand is not None
        and -1.0 <= cand.direction <= 1.0
        and 0.0 <= cand.confidence <= 1.0
    )
    if ok:
        res.ok("consensus_alpha_bounded")
    else:
        res.fail("consensus_alpha_bounded", f"verdict={verdict.payload()} cand={cand}")


def test_clean_cut(res: _Res) -> None:
    print("\n[4] CLEAN CUT — runtime logic modules free of bridges.vendor_shims")
    runtime_logic = [
        "ai/meta/core_ai.py",
        "training/evaluation/backtest.py",
        "econith_quant/bridge/ai_bridge.py",
        "econith/quant/routing/router.py",
        "econith/quant/consensus/kernel.py",
        "econith/world/mesa_kernel.py",
        "econith/world/abides_kernel.py",
    ]
    offenders: list[str] = []
    for rel in runtime_logic:
        path = _ROOT / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("bridges.vendor_shims"):
                offenders.append(rel)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("bridges.vendor_shims"):
                        offenders.append(rel)
    if not offenders:
        res.ok("no_vendor_shims_import_in_runtime_logic")
    else:
        res.fail("no_vendor_shims_import_in_runtime_logic", f"{sorted(set(offenders))}")


def main() -> int:
    print("== ECONITH Native Kernelization Parity Check ==")
    res = _Res()
    test_route_parity(res)
    test_friction(res)
    test_consensus_alpha(res)
    test_clean_cut(res)
    print("\n" + "=" * 56)
    if res.failed:
        print(f"  PARITY FAILURES: {res.failed}")
        return 1
    print("  All parity + clean-cut checks passed (0% logic drift).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
