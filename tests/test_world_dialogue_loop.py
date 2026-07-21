"""Causal dialogue loop: material triggers, grounding, cast, multi-turn."""
from __future__ import annotations

import json

from econith.world.agents.state_arrays import EmergentEvent
from econith.world.core.dialogue_orchestrator import DialogueOrchestrator, is_material
from econith.world.core.dialogue_schema import GroundedMetric
from econith.world.core.dialogue_validator import (
    build_cast,
    build_fallback_bundle,
    decisions_to_directives,
    parse_dialogue_json,
    strip_ungrounded_numbers,
)


def test_is_material_requires_intensity_and_kind() -> None:
    weak = [EmergentEvent(node="USA", kind="demand_contraction", intensity=0.2)]
    ok, _ = is_material(weak)
    assert ok is False
    strong = [EmergentEvent(node="USA", kind="labor_strike", intensity=0.9)]
    ok, reason = is_material(strong)
    assert ok is True
    assert "labor_strike" in reason


def test_strip_ungrounded_numbers() -> None:
    metrics = [GroundedMetric("intensity", 0.87, "", 1, "USA:labor_strike")]
    text = "USA unrest hit 87% then somehow 42.5% overnight"
    cleaned = strip_ungrounded_numbers(text, metrics)
    assert "87" in cleaned or "0.87" in cleaned or "87%" in cleaned
    assert "42.5" not in cleaned


def test_parse_multi_turn_requires_grounding_and_chains() -> None:
    raw = json.dumps(
        {
            "turns": [
                {
                    "agent_id": "labor-USA",
                    "role": "Labor",
                    "country": "USA",
                    "text": "Dissatisfaction is 0.90 — we resist the wage freeze.",
                    "responds_to": None,
                    "action_id": "bargain_resist",
                    "rationale": "protect real wages",
                    "confidence": 0.8,
                    "grounding": ["USA:labor_strike"],
                },
                {
                    "agent_id": "corp-USA",
                    "role": "Corporate",
                    "country": "USA",
                    "text": "Margins cannot absorb that ask; we hold.",
                    "responds_to": "labor-USA",
                    "action_id": "bargain_accept",
                    "rationale": "cost pressure",
                    "confidence": 0.7,
                    "grounding": ["USA:labor_strike"],
                },
                {
                    "agent_id": "cb-USA",
                    "role": "Central Bank",
                    "country": "USA",
                    "text": "Inflation is 0.03; we hold the policy rate for now.",
                    "responds_to": "corp-USA",
                    "action_id": "hold",
                    "rationale": "wait for wage pass-through",
                    "confidence": 0.6,
                    "grounding": ["USA:labor_strike"],
                },
                {
                    "agent_id": "bad",
                    "role": "Labor",
                    "country": "USA",
                    "text": "Print money forever at 99%.",
                    "responds_to": "cb-USA",
                    "action_id": "print_money_forever",
                    "grounding": ["USA:labor_strike"],
                },
            ]
        }
    )
    events = [
        EmergentEvent(
            node="USA",
            kind="labor_strike",
            intensity=0.9,
            metrics={"dissatisfaction": 0.9},
        )
    ]
    cast = build_cast(events, locale="en")
    grounded = [
        GroundedMetric("intensity", 0.9, "", 3, "USA:labor_strike"),
        GroundedMetric("dissatisfaction", 0.9, "", 3, "USA:labor_strike"),
        GroundedMetric("inflation_cpi", 0.03, "", 3, "USA:labor_strike"),
    ]
    decisions, utterances, rejected = parse_dialogue_json(
        raw,
        valid_codes={"USA", "CHN"},
        grounded=grounded,
        tick=3,
        locale="en",
        cast=cast,
    )
    assert len(utterances) >= 3
    assert any(u.responds_to == "labor-USA" for u in utterances)
    assert rejected >= 1
    # Labor bargain must NOT become a rate hike directive.
    dirs = decisions_to_directives(decisions, cast=cast)
    assert "USA" not in dirs or abs(dirs["USA"].interest_rate_delta) < 1e-9


def test_hallucinated_rate_in_utterance_stripped() -> None:
    raw = json.dumps(
        {
            "turns": [
                {
                    "agent_id": "cb-USA",
                    "role": "Central Bank",
                    "country": "USA",
                    "text": "We hike the policy rate to 25.0% immediately.",
                    "responds_to": None,
                    "action_id": "tighten",
                    "grounding": ["USA:labor_strike"],
                    "confidence": 0.5,
                }
            ]
        }
    )
    grounded = [
        GroundedMetric("intensity", 0.7, "", 1, "USA:labor_strike"),
    ]
    cast = build_cast(
        [EmergentEvent(node="USA", kind="labor_strike", intensity=0.7)],
        locale="en",
    )
    _, utterances, _ = parse_dialogue_json(
        raw, valid_codes={"USA"}, grounded=grounded, tick=1, cast=cast
    )
    assert utterances
    assert "25.0" not in utterances[0].text
    assert "25%" not in utterances[0].text


def test_fallback_is_status_only_no_hardcode_policy() -> None:
    events = [
        EmergentEvent(
            node="USA",
            kind="labor_strike",
            intensity=0.91,
            metrics={"dissatisfaction": 0.91, "consumption_index": 0.8},
        )
    ]
    bundle = build_fallback_bundle(tick=10, events=events, locale="en", material_reason="x")
    assert bundle.decisions == []
    assert bundle.utterances
    assert bundle.source == "status"
    assert bundle.cast

    applied: list = []
    orch = DialogueOrchestrator(
        valid_codes=("USA", "CHN"),
        pool=None,
        on_directives=lambda d: applied.append(d),
        cooldown_ticks=1,
    )
    out = orch.maybe_schedule(
        tick=1,
        events=events,
        macro={"USA": {"interest_rate": 0.04, "inflation_cpi": 0.03, "gdp_growth": 0.01, "unemployment": 0.06}},
        locale="en",
    )
    assert out is not None
    # Status fallback must NOT push hardcode directives into physics.
    assert applied == []
    out2 = orch.maybe_schedule(
        tick=1,
        events=events,
        macro={"USA": {"interest_rate": 0.04, "inflation_cpi": 0.03, "gdp_growth": 0.01, "unemployment": 0.06}},
        locale="en",
    )
    assert out2 is out


def test_cast_is_dynamic_from_events_not_fixed_four_hubs() -> None:
    events = [
        EmergentEvent(node="IND", kind="demand_contraction", intensity=0.8, metrics={"consumption_index": 0.7}),
        EmergentEvent(node="JPN", kind="labor_strike", intensity=0.7, metrics={"dissatisfaction": 0.7}),
    ]
    cast = build_cast(events, locale="en")
    countries = {p.country for p in cast}
    assert countries <= {"IND", "JPN"}
    assert "USA" not in countries
    assert any(p.can_set_policy for p in cast)
    assert any(not p.can_set_policy for p in cast)


def test_journalist_info_admitted_by_telemetry_handler() -> None:
    """Regression: info-level journalist.news must reach world_events."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from core.event_bus import Event
    from core.telemetry import MetricsHub

    bus = MagicMock()
    time_engine = MagicMock()
    hub = MetricsHub(bus, time_engine)
    event = Event(
        topic="journalist.news",
        payload={
            "level": "info",
            "category": "MACRO",
            "message": "CPI drifts across major hubs.",
            "locale": "vi",
        },
        ts=datetime.now(timezone.utc),
    )
    import asyncio

    asyncio.run(hub._on_journalist_news(event))
    snap = hub.snapshot()
    assert snap["world_events"]
    assert snap["world_events"][0]["source"] == "journalist"
    assert snap["world_events"][0]["level"] == "info"
    assert "CPI" in snap["world_events"][0]["message"]
