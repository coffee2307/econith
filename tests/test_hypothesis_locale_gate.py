"""Locale hard-gate: Vietnamese Agents feed never keeps English LLM prose."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ai.simulator_engine.hypothesis_i18n import (
    vi_act_line,
    vi_ask_line,
    vi_fallback_for_phase,
)
from ai.simulator_engine.hypothesis_schema import Hypothesis, HypothesisOutcome
from ai.simulator_engine.hypothesis_thinker import (
    compose_dialogue_lines,
    dialogue_lines_for_cycle,
)
from ai.simulator_engine.operator_voice import (
    ensure_locale_text,
    is_operator_locale,
    rewrite_line,
)


class _EnglishPool:
    """LLM stub that always answers in English."""

    def create_chat_completion(self, **kwargs):  # noqa: ANN003
        content = (
            '{"ask":"If I implement a monetary policy change in Japan, what happens?",'
            '"act":"The corporate desk applied a fiscal policy change in the United States.",'
            '"judge":"The assessment indicates growth may slow while inflation eases."}'
        )
        if kwargs.get("messages"):
            sys = (kwargs["messages"][0].get("content") or "").lower()
            if "translator" in sys or "dịch" in sys or "translate" in sys:
                content = (
                    "The corporate desk applied a fiscal policy change in the United States "
                    "by increasing the tax rate to 0.04."
                )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def test_is_operator_locale_pure_english_false() -> None:
    en = (
        "The corporate desk applied a fiscal policy change in the United States "
        "by increasing the tax rate to 0.04, aiming to curb inflation."
    )
    assert not is_operator_locale(en, "vi")
    ask = "If I implement a monetary policy change in Japan, what happens to growth?"
    assert not is_operator_locale(ask, "vi")


def test_is_operator_locale_pure_vietnamese_true() -> None:
    vi = (
        "Doanh nghiệp tiến hành điều chỉnh: đặt USA.tax = 0.04. "
        "Theo dõi lan tỏa sang tăng trưởng, thất nghiệp, lạm phát."
    )
    assert is_operator_locale(vi, "vi")


def test_is_operator_locale_mixed_english_with_one_diacritic_false() -> None:
    mixed = "The corporate desk đã applied a fiscal policy change in the United States."
    assert not is_operator_locale(mixed, "vi")


def test_ensure_locale_text_uses_fallback_not_english() -> None:
    pool = _EnglishPool()
    fallback = vi_ask_line(
        Hypothesis(
            id="x",
            prompt="shock",
            subjects=["USA", "SAU"],
            mutations=[{"country": "USA", "field": "tax", "value": 0.04}],
        )
    )
    out = ensure_locale_text(
        pool,
        base_url="http://localhost",
        model="llama3:8b",
        locale="vi",
        text="If I implement a monetary policy change in Japan, what happens?",
        context="ask",
        fallback=fallback,
    )
    assert is_operator_locale(out, "vi")
    assert "If I" not in out
    assert out == fallback


def test_rewrite_line_returns_empty_on_vi_failure() -> None:
    pool = _EnglishPool()
    out = rewrite_line(
        pool,
        base_url="http://localhost",
        model="llama3:8b",
        locale="vi",
        source_text="The corporate desk applied a fiscal policy change.",
        context="act",
    )
    assert out == ""


def test_dialogue_fallback_replaces_english_when_locale_vi() -> None:
    hyp = Hypothesis(
        id="h1",
        prompt="USA tax hike",
        rationale="ASK: If I raise tax in USA, what happens?\nPLAN: mutations=[]",
        subjects=["USA", "SAU", "DEU"],
        mutations=[{"country": "USA", "field": "tax", "value": 0.04}],
    )
    outcome = HypothesisOutcome(hypothesis_id="h1", status="ok", deltas={"USA.tax": 0.01})
    reflection = {
        "assessment": "The assessment indicates spillover effects across markets.",
        "next_question": "If I cut rates in Japan, what happens?",
        "action_line": "The corporate desk applied a fiscal policy change.",
    }
    lines = dialogue_lines_for_cycle(hyp, outcome, reflection, locale="vi")
    assert len(lines) == 3
    for line in lines:
        assert is_operator_locale(line["text"], "vi"), line
        assert "If I" not in line["text"]
        assert "corporate desk" not in line["text"].lower()


def test_vi_fallback_for_phase_ask_act_judge() -> None:
    hyp = Hypothesis(
        id="h2",
        prompt="p",
        subjects=["USA"],
        mutations=[{"country": "USA", "field": "tax", "value": 0.04}],
    )
    ask = vi_fallback_for_phase("ask", hyp)
    act = vi_fallback_for_phase("act", hyp)
    judge = vi_fallback_for_phase("judge", hyp)
    assert is_operator_locale(ask, "vi")
    assert is_operator_locale(act, "vi")
    assert is_operator_locale(judge, "vi")
    assert "USA.tax" in act or "0.04" in act


def test_compose_dialogue_with_english_llm_still_vietnamese() -> None:
    hyp = Hypothesis(
        id="h3",
        prompt="USA tax",
        rationale="ASK: raise tax\nPLAN: mutations=[]",
        subjects=["USA", "SAU", "DEU"],
        mutations=[{"country": "USA", "field": "tax", "value": 0.04}],
    )
    outcome = HypothesisOutcome(
        hypothesis_id="h3", status="ok", deltas={"USA.inflation": -0.001}
    )
    reflection = {
        "assessment": "ok",
        "next_question": "next?",
        "action_line": vi_act_line(hyp),
    }
    thinker = SimpleNamespace(
        _base=SimpleNamespace(
            _use_llm=True,
            _llm_pool=_EnglishPool(),
            _llm_base_url="http://localhost",
            _llm_model="llama3:8b",
        )
    )
    lines = asyncio.run(
        compose_dialogue_lines(thinker, hyp, outcome, reflection, locale="vi")
    )
    assert len(lines) == 3
    for line in lines:
        assert is_operator_locale(line["text"], "vi"), line["text"]
        assert "If I implement" not in line["text"]
        assert "corporate desk applied" not in line["text"].lower()


def test_publish_lines_keeps_english_and_attaches_text_vi() -> None:
    """LLM speaks English; Vietnamese lands in text_vi — lines are never dropped."""
    from ai.simulator_engine.hypothesis_runner import HypothesisRunner
    from ai.simulator_engine.llm_scenario import LLMScenarioEngine
    from ai.simulator_engine.world_kernel import WorldKernel
    from core.event_bus import EventBus
    from core.system_controller import OperatingMode, SystemController

    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    scenario = LLMScenarioEngine(bus, kernel)
    ctrl = SystemController()
    ctrl.set_mode(OperatingMode.SIMULATION)
    runner = HypothesisRunner(bus, kernel, scenario, controller=ctrl)
    runner._generator._llm_pool = None  # type: ignore[attr-defined]

    published: list[dict] = []

    async def _fake_publish(topic: str, **payload: object) -> None:
        if topic == "world.agent.narrative":
            published.append(dict(payload))

    runner._bus.publish = _fake_publish  # type: ignore[method-assign]
    hyp = Hypothesis(
        id="dual",
        prompt="USA tax hike",
        subjects=["USA", "SAU"],
        mutations=[{"country": "USA", "field": "tax", "value": 0.04}],
    )
    lines = [
        {
            "actor": "Corporate AI",
            "country": "USA+SAU",
            "text": (
                "The corporate desk applied a fiscal policy change "
                "in the United States."
            ),
            "phase": "act",
        },
        {
            "actor": "Government AI",
            "country": "USA+SAU",
            "text": "If I implement a monetary policy change in Japan, what happens?",
            "phase": "ask",
        },
    ]
    asyncio.run(runner._publish_lines(lines, level="info", locale="vi", hyp=hyp))
    assert len(published) == 2
    for row in published:
        assert isinstance(row["text"], str) and row["text"]
        assert "corporate desk" in str(row["text"]).lower() or "If I" in str(row["text"])
        assert is_operator_locale(str(row["text_vi"]), "vi")
        assert row.get("locale") == "en"
