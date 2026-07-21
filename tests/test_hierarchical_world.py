"""Regression: LLM directives must be one-shot within TTL, not cumulative."""
from __future__ import annotations

from econith.world.core.hierarchy_broker import GovernorDirective, HierarchyBroker


def _macro(rate: float = 0.03) -> dict[str, dict[str, float]]:
    codes = HierarchyBroker()._codes  # noqa: SLF001 - test introspection only
    return {
        c: {
            "interest_rate": rate,
            "inflation_cpi": 0.025,
            "inflation_target": 0.02,
            "gdp_growth": 0.02,
            "unemployment": 0.05,
            "political_stability": 0.6,
            "govt_debt_to_gdp": 0.9,
            "corporate_tax": 0.2,
            "union_density": 0.2,
            "productivity_index": 100.0,
            "labor_cost_index": 100.0,
            "capital_control": 0.0,
            "txn_friction": 0.1,
            "money_supply_m2": 4e12,
            "gdp": 1e13,
        }
        for c in codes
    }


def test_llm_directive_applies_once_not_every_ttl_tick() -> None:
    broker = HierarchyBroker()
    macro = _macro(0.03)
    code = broker._codes[0]  # noqa: SLF001
    control = broker._tier1_control_law(macro, 0.0)

    tighten = GovernorDirective(
        code=code,
        interest_rate_delta=0.005,
        tariff_delta=0.0,
        money_supply_delta=-0.01,
        tax_delta=0.0,
        stance=0.5,
        rationale="test",
    ).clamped()
    broker.set_llm_directives({code: tighten}, ttl_ticks=20)

    # Tick 1: directive should fire once.
    blended1 = broker._blend_directives(control)  # noqa: SLF001
    assert abs(blended1[code].interest_rate_delta - 0.005) < 1e-9

    # Ticks 2..21: same directive within TTL must NOT re-apply; control law wins.
    broker._tick = 5  # noqa: SLF001
    blended2 = broker._blend_directives(control)  # noqa: SLF001
    assert abs(blended2[code].interest_rate_delta - control[code].interest_rate_delta) < 1e-9

    # A NEW directive (different fingerprint) is allowed once.
    broker.set_llm_directives(
        {
            code: GovernorDirective(
                code=code,
                interest_rate_delta=0.010,
                stance=0.7,
            ).clamped()
        },
        ttl_ticks=20,
    )
    blended3 = broker._blend_directives(control)  # noqa: SLF001
    assert abs(blended3[code].interest_rate_delta - 0.010) < 1e-9

    # After expiry, control law alone.
    broker._tick = 99  # noqa: SLF001
    blended4 = broker._blend_directives(control)  # noqa: SLF001
    assert blended4[code] is control[code]
