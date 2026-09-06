"""HypothesisRunner: self-authored shocks → scenario → measured outcomes."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ai.simulator_engine.hypothesis_generator import HypothesisGenerator
from ai.simulator_engine.hypothesis_runner import HypothesisRunner
from ai.simulator_engine.llm_scenario import LLMScenarioEngine
from ai.simulator_engine.rollout_export import SealedRolloutWriter
from ai.simulator_engine.world_kernel import WorldKernel
from core.event_bus import EventBus
from core.system_controller import (
    AUTONOMOUS_HYPOTHESIS_IMPLEMENTED,
    OperatingMode,
    SystemController,
)


def test_autonomous_hypothesis_implemented_flag() -> None:
    assert AUTONOMOUS_HYPOTHESIS_IMPLEMENTED is True
    ctrl = SystemController()
    snap = ctrl.snapshot()
    assert snap.autonomous_hypothesis_implemented is True
    assert snap.as_dict()["autonomous_hypothesis_implemented"] is True


def test_generator_combinatorial_is_parseable() -> None:
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    gen = HypothesisGenerator(kernel, rng=__import__("random").Random(7))
    hyp = gen.generate()
    assert hyp.prompt.strip()
    assert hyp.generator == "combinatorial"
    assert "climate crisis" not in hyp.prompt.lower()
    engine = LLMScenarioEngine(bus, kernel)
    parsed = engine.parse(hyp.prompt)
    assert parsed.prompt
    assert 0.0 <= parsed.severity <= 1.0


def test_runner_skips_when_world_off() -> None:
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    scenario = LLMScenarioEngine(bus, kernel)
    ctrl = SystemController()
    ctrl.set_mode(OperatingMode.AUTONOMOUS_HYPOTHESIS)
    ctrl.set_world_simulation(False)
    runner = HypothesisRunner(
        bus,
        kernel,
        scenario,
        controller=ctrl,
        settle_sec=0.0,
        interval_sec=9999,
        rollout_writer=SealedRolloutWriter(root=Path("data/rollouts_test_skip")),
    )

    async def _run():
        return await runner.run_once(force=False)

    outcome = asyncio.run(_run())
    assert outcome.status == "skipped"


def test_runner_records_ok_when_forced(tmp_path: Path) -> None:
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    scenario = LLMScenarioEngine(bus, kernel)
    ctrl = SystemController()
    ctrl.set_mode(OperatingMode.SIMULATION)
    ctrl.set_world_simulation(False)  # not armed — force bypasses arm check
    writer = SealedRolloutWriter(root=tmp_path)
    runner = HypothesisRunner(
        bus,
        kernel,
        scenario,
        controller=ctrl,
        generator=HypothesisGenerator(kernel, rng=__import__("random").Random(3)),
        settle_sec=0.0,
        interval_sec=9999,
        rollout_writer=writer,
    )

    async def _run():
        return await runner.run_once(force=True)

    outcome = asyncio.run(_run())
    assert outcome.status == "ok"
    assert outcome.hypothesis_id
    report = runner.report_dict()
    assert report["total_ok"] >= 1
    assert report["last_hypothesis"] is not None
    files = list(tmp_path.glob("world_hypotheses_*.jsonl"))
    assert files, "sealed rollout JSONL should be written on ok"


def test_runner_armed_only_with_mode_and_world() -> None:
    bus = EventBus()
    kernel = WorldKernel(bus, event_probability=0.0)
    scenario = LLMScenarioEngine(bus, kernel)
    ctrl = SystemController()
    runner = HypothesisRunner(bus, kernel, scenario, controller=ctrl, settle_sec=0.0)
    ctrl.set_mode(OperatingMode.REALITY)
    ctrl.set_world_simulation(True)
    assert runner.is_armed() is False
    ctrl.set_mode(OperatingMode.AUTONOMOUS_HYPOTHESIS)
    assert runner.is_armed() is True
    ctrl.set_world_simulation(False)
    assert runner.is_armed() is False
