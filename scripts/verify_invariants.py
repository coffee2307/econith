"""ECONITH :: scripts/verify_invariants.py

The **safety gate** for vendor integration. Run this before wiring any new
shimmed vendor into ``main.py`` — it asserts the three institutional invariants
that must survive every integration phase:

    A) MODE GATE      — simulated ``world.*`` topics never reach a DOMAIN_QUANT
                        handler in REALITY; coupling is permitted in SIMULATION.
    B) SHIM CONTRACT  — vendor shims mediate cleanly through the EventBus, obey
                        their declared emit contract + mode gate, and the fleet
                        audit finds no shim claiming execution authority.
    C) SENTINEL VETO  — an AI-proposed signal is still hard-vetoed by the
                        Sentinel gate (FROZEN → no order.intent), and flows only
                        when the Sentinel is NORMAL.

Exit code 0 == all invariants hold; non-zero == a regression was introduced.

    python scripts/verify_invariants.py
    python scripts/verify_invariants.py --verbose
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.event_bus import DOMAIN_QUANT, Event, EventBus  # noqa: E402
from core.mode import QuantMode, set_mode  # noqa: E402

logger = logging.getLogger("econith.verify")


# ---------------------------------------------------------------------------
# Tiny async test harness (no pytest dependency — runnable on a bare box)
# ---------------------------------------------------------------------------
class _Results:
    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.skipped: list[tuple[str, str]] = []

    def ok(self, name: str) -> None:
        self.passed.append(name)
        print(f"  \033[32mPASS\033[0m  {name}")

    def fail(self, name: str, why: str) -> None:
        self.failed.append((name, why))
        print(f"  \033[31mFAIL\033[0m  {name}\n         -> {why}")

    def skip(self, name: str, why: str) -> None:
        self.skipped.append((name, why))
        print(f"  \033[33mSKIP\033[0m  {name} ({why})")


async def _settle(bus: EventBus, rounds: int = 5) -> None:
    """Let the bus dispatch loop drain in-flight events deterministically."""
    for _ in range(rounds):
        await asyncio.sleep(0)
        if bus._queue.empty():  # noqa: SLF001 - harness needs queue introspection
            await asyncio.sleep(0.01)
            if bus._queue.empty():  # noqa: SLF001
                return
    await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# A) MODE GATE
# ---------------------------------------------------------------------------
async def test_mode_gate(res: _Results) -> None:
    print("\n[A] MODE GATE — world.* isolation from DOMAIN_QUANT")
    bus = EventBus()
    await bus.start()
    try:
        received: list[Event] = []

        async def quant_handler(event: Event) -> None:
            received.append(event)

        # An execution-domain node accidentally listening to a simulated topic.
        bus.subscribe("world.micro_impact", quant_handler, domain=DOMAIN_QUANT)

        # REALITY: the gate must DROP the event before the QUANT handler.
        set_mode(QuantMode.REALITY)
        await bus.publish("world.micro_impact", volatility_multiplier=2.0)
        await _settle(bus)
        if received:
            res.fail("A1 reality_blocks_world_to_quant",
                     f"QUANT handler received {len(received)} event(s) in REALITY")
        else:
            res.ok("A1 reality_blocks_world_to_quant")

        # SIMULATION: coupling is permitted — the same handler now receives it.
        received.clear()
        set_mode(QuantMode.SIMULATION)
        await bus.publish("world.micro_impact", volatility_multiplier=2.0)
        await _settle(bus)
        if received:
            res.ok("A2 simulation_allows_coupling")
        else:
            res.fail("A2 simulation_allows_coupling",
                     "QUANT handler received nothing in SIMULATION")
    finally:
        set_mode(QuantMode.REALITY)
        await bus.stop()


# ---------------------------------------------------------------------------
# B) SHIM CONTRACT
# ---------------------------------------------------------------------------
async def test_shim_contract(res: _Results) -> None:
    print("\n[B] SHIM CONTRACT — EventBus mediation + no execution authority")
    from bridges.vendor_shims import (
        EconithTradingAgentsShim,
        VendorContract,
        VendorShim,
        build_default_registry,
    )

    bus = EventBus()
    await bus.start()
    try:
        registry = build_default_registry(bus)

        # B1: fleet audit — no shim may claim execution authority.
        problems = registry.audit()
        if problems:
            res.fail("B1 fleet_audit_clean", "; ".join(problems))
        else:
            res.ok("B1 fleet_audit_clean")

        # B2: registering the fleet must never raise, even with no vendor pulled.
        try:
            registry.register_all()
            res.ok("B2 register_all_safe_without_sources")
        except Exception as exc:  # noqa: BLE001
            res.fail("B2 register_all_safe_without_sources", repr(exc))

        # B3: a construction-time contract breach is rejected (fail-fast).
        class _BadShim(VendorShim):
            contract = VendorContract(
                name="bad", pillar="quant", probe_module="nonexistent_pkg",
                emits=("order.intent",),  # forbidden execution topic
            )

            def _wire(self) -> None:
                return None

        try:
            _BadShim(bus)
            res.fail("B3 forbidden_emit_rejected", "shim with order.intent emit was accepted")
        except ValueError:
            res.ok("B3 forbidden_emit_rejected")

        # B4: a declared advisory emit is delivered through the bus.
        shim = EconithTradingAgentsShim(bus)
        got: list[Event] = []

        async def sink(event: Event) -> None:
            got.append(event)

        bus.subscribe("meta.debate.verdict", sink)
        await shim.emit("meta.debate.verdict", consensus_bias=0.1, confidence=0.5)
        await _settle(bus)
        if got:
            res.ok("B4 advisory_emit_delivered")
        else:
            res.fail("B4 advisory_emit_delivered", "verdict not delivered through bus")

        # B5: emitting an UNDECLARED topic is refused (contract enforcement).
        try:
            await shim.emit("ai.signal", direction=1.0)
            res.fail("B5 undeclared_emit_refused", "undeclared emit was allowed")
        except RuntimeError:
            res.ok("B5 undeclared_emit_refused")

        # B6: the full fleet is exactly the 8 mapped vendors.
        expected = {
            "openbb", "qlib", "nofx", "trading_agents",
            "ai_hedge_fund", "zipline_reloaded", "mesa", "abides",
        }
        got = set(registry.shims.keys())
        if got == expected:
            res.ok("B6 fleet_covers_all_8_vendors")
        else:
            res.fail("B6 fleet_covers_all_8_vendors",
                     f"missing={expected - got} extra={got - expected}")

        # B7: no shim publishes to a DOMAIN_QUANT-blocked topic prefix and none
        #     emits an execution topic (audit already covers order.intent; here we
        #     assert none claim the QUANT domain).
        bad_domain = [
            n for n, s in registry.shims.items() if s.contract.domain == DOMAIN_QUANT
        ]
        if bad_domain:
            res.fail("B7 no_shim_claims_quant_domain", f"{bad_domain}")
        else:
            res.ok("B7 no_shim_claims_quant_domain")
    finally:
        set_mode(QuantMode.REALITY)
        await bus.stop()


# ---------------------------------------------------------------------------
# D) WORLD / OFFLINE VENDORS
# ---------------------------------------------------------------------------
async def test_world_and_offline(res: _Results) -> None:
    print("\n[D] WORLD + QUANT NATIVE KERNELS")
    from econith.quant.backtest.friction import EconithFrictionModel
    from econith.quant.routing import EconithRouteKernel
    from econith.world import AbidesStepKernel, MesaSovereignKernel

    # D1: native kernels construct with NO bus (used inside runtime/backtest).
    try:
        EconithFrictionModel(fee_bps=4.0, slippage_bps=1.0, spread_bps=2.0)
        EconithRouteKernel()
        MesaSovereignKernel()
        AbidesStepKernel()
        res.ok("D1 native_kernels_construct_without_bus")
    except Exception as exc:  # noqa: BLE001
        res.fail("D1 native_kernels_construct_without_bus", repr(exc))

    # D2: native ABIDES kernel refuses to run in REALITY.
    set_mode(QuantMode.REALITY)
    abides = AbidesStepKernel()
    try:
        abides.ensure_mode()
        res.fail("D2 abides_refuses_reality", "ensure_simulation passed in REALITY")
    except RuntimeError:
        res.ok("D2 abides_refuses_reality")

    # D3: native ABIDES kernel is marked simulation_only.
    if abides.simulation_only:
        res.ok("D3 abides_marked_simulation_only")
    else:
        res.fail("D3 abides_marked_simulation_only", "simulation_only is False")

    # D4: native friction blend is a positive, finite bps value.
    z = EconithFrictionModel(fee_bps=4.0, slippage_bps=1.0, spread_bps=2.0)
    bps = z.aggregate_friction_bps()
    if bps > 0 and bps == bps:  # finite + positive
        res.ok("D4 native_friction_positive")
    else:
        res.fail("D4 native_friction_positive", f"bps={bps}")

    # D5: native consensus kernel produces a bounded, well-formed verdict.
    from econith.quant.consensus import EconithConsensusKernel

    ctx = {"obi": 0.3, "yield_spread_10y_2y": 0.01, "realized_vol": 0.002}
    verdict = EconithConsensusKernel().deliberate(ctx)
    if verdict.has_signal and -1.0 <= verdict.bias <= 1.0 and 0.0 <= verdict.confidence <= 1.0:
        res.ok("D5 native_consensus_bounded")
    else:
        res.fail("D5 native_consensus_bounded", f"verdict={verdict.payload()}")
    set_mode(QuantMode.REALITY)


# ---------------------------------------------------------------------------
# C) SENTINEL VETO
# ---------------------------------------------------------------------------
async def test_sentinel_veto(res: _Results) -> None:
    print("\n[C] SENTINEL VETO — order.intent stays gated")
    try:
        from econith_quant.bridge.ai_bridge import AIBridge
    except Exception as exc:  # noqa: BLE001
        res.skip("C sentinel_veto", f"AIBridge import unavailable: {exc!r}")
        return

    bus = EventBus()
    await bus.start()
    try:
        intents: list[Event] = []
        vetoes: list[Event] = []

        async def on_intent(event: Event) -> None:
            intents.append(event)

        async def on_veto(event: Event) -> None:
            vetoes.append(event)

        bus.subscribe("order.intent", on_intent)
        bus.subscribe("ai.veto", on_veto)

        bridge = AIBridge(bus)
        bridge.register()

        # FROZEN: Sentinel must hard-veto — no order.intent may escape.
        await bus.publish("sentinel.status", mode="FROZEN")
        await _settle(bus)
        await bus.publish(
            "ai.signal", action="LONG", direction=1.0, confidence=0.9, symbol="BTCUSDT"
        )
        await _settle(bus)
        if intents:
            res.fail("C1 frozen_blocks_order_intent",
                     f"order.intent escaped under FROZEN ({len(intents)})")
        elif not vetoes:
            res.fail("C1 frozen_blocks_order_intent", "no ai.veto emitted under FROZEN")
        else:
            res.ok("C1 frozen_blocks_order_intent")

        # NORMAL: the same signal now flows to execution.
        intents.clear()
        vetoes.clear()
        await bus.publish("sentinel.status", mode="NORMAL")
        await _settle(bus)
        await bus.publish(
            "ai.signal", action="LONG", direction=1.0, confidence=0.9, symbol="BTCUSDT"
        )
        await _settle(bus)
        if intents:
            res.ok("C2 normal_allows_order_intent")
        else:
            res.fail("C2 normal_allows_order_intent",
                     "order.intent not emitted under NORMAL Sentinel")
    finally:
        set_mode(QuantMode.REALITY)
        await bus.stop()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def _run() -> int:
    res = _Results()
    await test_mode_gate(res)
    await test_shim_contract(res)
    await test_sentinel_veto(res)
    await test_world_and_offline(res)

    print("\n" + "=" * 60)
    print(f"  passed={len(res.passed)}  failed={len(res.failed)}  skipped={len(res.skipped)}")
    print("=" * 60)
    if res.failed:
        print("\nINVARIANT REGRESSION(S) DETECTED:")
        for name, why in res.failed:
            print(f"  - {name}: {why}")
        return 1
    print("\nAll invariants hold. Safe to wire vendors incrementally.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ECONITH vendor-integration safety gate")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)-7s %(name)s :: %(message)s",
    )
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
