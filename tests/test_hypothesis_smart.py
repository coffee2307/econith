"""Smarter Hypothesis AI: memory scoring, curriculum, debate, enriched rollouts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai.simulator_engine.hypothesis_debate import (
    debate_dialogue_lines,
    debate_hypothesis,
    debate_summary,
    soften_mutations,
)
from ai.simulator_engine.hypothesis_generator import HypothesisGenerator
from ai.simulator_engine.hypothesis_runner import HypothesisRunner
from ai.simulator_engine.hypothesis_schema import Hypothesis
from ai.simulator_engine.hypothesis_thinker import (
    ExperimentMemory,
    ScientificHypothesisThinker,
    chapter_for_run,
    mutation_fingerprint,
    signal_score,
)
from ai.simulator_engine.llm_scenario import LLMScenarioEngine
from ai.simulator_engine.rollout_export import SealedRolloutWriter
from ai.simulator_engine.world_kernel import WorldKernel
from core.event_bus import EventBus
from core.system_controller import OperatingMode, SystemController


def test_fingerprint_and_signal_score() -> None:
    a = [{"country": "usa", "field": "interest_rate", "value": 0.06, "direction": "up"}]
    b = [{"country": "USA", "field": "interest_rate", "value": 0.09, "direction": "up"}]
    c = [{"country": "USA", "field": "interest_rate", "value": 0.01, "direction": "down"}]
    assert mutation_fingerprint(a) == mutation_fingerprint(b)
    assert mutation_fingerprint(a) != mutation_fingerprint(c)
    assert mutation_fingerprint([]) == "(none)"
    assert signal_score({}) == 0.0
    assert signal_score({"USA.inflation": 0.2}) > signal_score({"USA.inflation": 0.001})


def test_chapters_rotate() -> None:
    names = [chapter_for_run(i)[0] for i in range(8)]
    assert names[:4] == ["monetary", "fiscal_trade", "spillover", "reversal"]
    assert names[4:] == names[:4]


def test_memory_persists_and_scores(tmp_path: Path) -> None:
    path = tmp_path / "hypothesis_memory.json"
    mem = ExperimentMemory(maxlen=4, path=path)
    mem.remember(
        question="What if USA hikes?",
        mutations=[{"country": "USA", "field": "interest_rate", "value": 0.06, "direction": "up"}],
        deltas={"USA.inflation": 0.05},
        assessment="clear",
        next_question="try CHN",
        chapter="monetary",
    )
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["runs"] == 1
    assert payload["items"][0]["fingerprint"] == "USA.interest_rate/up"
    assert payload["items"][0]["signal_score"] > 0

    reloaded = ExperimentMemory(maxlen=4, path=path)
    assert reloaded.runs == 1
    assert "USA.interest_rate/up" in reloaded.avoid_fingerprints()
    # USA already probed → other hubs rank first as under-explored.
    assert reloaded.under_explored_hubs(["USA", "CHN", "DEU"], n=2) == ["CHN", "DEU"]
    assert (reloaded.strongest_recent() or {}).get("fingerprint") == "USA.interest_rate/up"


def test_memory_repeat_lowers_novelty(tmp_path: Path) -> None:
    mem = ExperimentMemory(maxlen=4, path=tmp_path / "m.json")
    mut = [{"country": "USA", "field": "tax", "value": 0.3, "direction": "up"}]
    for _ in range(2):
        mem.remember(
            question="q",
            mutations=mut,
            deltas={"USA.gdp_growth": -0.01},
            assessment="a",
            next_question="n",
            chapter="fiscal_trade",
        )
    assert mem.runs == 2
    assert "novelty" in mem.as_prompt_block()


def _thinker() -> ScientificHypothesisThinker:
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    gen = HypothesisGenerator(kernel, rng=__import__("random").Random(11))
    return ScientificHypothesisThinker(gen, memory=ExperimentMemory(persist=False))


def test_debate_heuristic_produces_four_turns() -> None:
    thinker = _thinker()
    hyp = Hypothesis(
        id="t1",
        prompt="USA raises rates",
        rationale="ASK: what happens?",
        severity=0.4,
        subjects=["USA"],
        mutations=[
            {"country": "USA", "field": "interest_rate", "value": 0.09, "direction": "up"}
        ],
    )
    out = asyncio.run(debate_hypothesis(thinker, hyp, locale="en"))
    actors = [t.actor for t in out.debate]
    assert actors == ["Government AI", "Corporate AI", "Societal AI", "Chair"]
    assert out.chair_decision in ("accept", "revise", "pivot")
    assert out.chair_reason
    assert out.fingerprint
    lines = debate_dialogue_lines(out)
    assert [line["phase"] for line in lines] == [
        "debate:gov",
        "debate:corp",
        "debate:society",
        "debate:chair",
    ]
    assert debate_summary(out)


def test_debate_vietnamese_when_locale_vi() -> None:
    thinker = _thinker()
    hyp = Hypothesis(id="t2", prompt="p", rationale="r", subjects=["VNM"])
    out = asyncio.run(debate_hypothesis(thinker, hyp, locale="vi"))
    assert any("Chính phủ" in t.text or "Xã hội" in t.text for t in out.debate)


def test_debate_softens_high_severity() -> None:
    thinker = _thinker()
    snapshot = thinker._base._kernel.state_dict()
    code = next(iter((snapshot.get("countries") or {}).keys()))
    hyp = Hypothesis(
        id="t3",
        prompt="extreme hike",
        rationale="r",
        severity=0.95,
        subjects=[code],
        mutations=[{"country": code, "field": "interest_rate", "value": 0.5, "direction": "up"}],
    )
    out = asyncio.run(debate_hypothesis(thinker, hyp, locale="en"))
    assert out.chair_decision == "revise"
    assert out.mutations[0]["value"] < 0.5


def test_soften_mutations_moves_toward_current() -> None:
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    snapshot = kernel.state_dict()
    code = next(iter((snapshot.get("countries") or {}).keys()))
    softened = soften_mutations(
        [{"country": code, "field": "interest_rate", "value": 0.5}], snapshot
    )
    assert softened and softened[0]["value"] < 0.5


def test_runner_cycle_enriches_rollout_and_memory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HYPOTHESIS_MEMORY_PATH", str(tmp_path / "mem.json"))
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    scenario = LLMScenarioEngine(bus, kernel)
    ctrl = SystemController()
    ctrl.set_mode(OperatingMode.SIMULATION)
    ctrl.set_world_simulation(False)
    runner = HypothesisRunner(
        bus,
        kernel,
        scenario,
        controller=ctrl,
        generator=HypothesisGenerator(kernel, rng=__import__("random").Random(5)),
        settle_sec=0.0,
        interval_sec=9999,
        rollout_writer=SealedRolloutWriter(root=tmp_path),
    )
    narratives: list[dict] = []
    original_publish = bus.publish

    async def _spy(topic: str, **payload):
        if topic == "world.agent.narrative":
            narratives.append(payload)
        await original_publish(topic, **payload)

    monkeypatch.setattr(bus, "publish", _spy)

    outcome = asyncio.run(runner.run_once(force=True))
    assert outcome.status == "ok"

    files = list(tmp_path.glob("world_hypotheses_*.jsonl"))
    assert files
    record = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])
    for key in ("mutations", "signal_score", "fingerprint", "chapter", "debate_summary"):
        assert key in record
    assert record["debate_summary"]

    provenances = [n.get("provenance", "") for n in narratives]
    assert "hypothesis:debate:gov" in provenances
    assert "hypothesis:debate:chair" in provenances
    assert any(p in provenances for p in ("hypothesis:ask", "hypothesis:act", "hypothesis:judge"))

    mem = json.loads((tmp_path / "mem.json").read_text(encoding="utf-8"))
    assert mem["runs"] >= 1
