"""ECONITH :: ai.simulator_engine.hypothesis_runner

Background loop: when AUTONOMOUS_HYPOTHESIS is armed and World is enabled,
generate a shock → run_scenario → settle → record measured deltas.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

from ai.simulator_engine.hypothesis_generator import HypothesisGenerator
from ai.simulator_engine.hypothesis_schema import (
    Hypothesis,
    HypothesisOutcome,
    HypothesisReport,
)
from ai.simulator_engine.hypothesis_debate import (
    debate_dialogue_lines,
    debate_hypothesis,
    debate_summary,
)
from ai.simulator_engine.hypothesis_thinker import (
    ScientificHypothesisThinker,
    compose_dialogue_lines,
    signal_score,
)
from ai.simulator_engine.llm_scenario import LLMScenarioEngine
from ai.simulator_engine.rollout_export import SealedRolloutWriter
from ai.simulator_engine.world_kernel import WorldKernel
from core.event_bus import EventBus
from core.system_controller import SystemController, get_system_controller

logger = logging.getLogger("econith.world.hypothesis_runner")

_TRACK_FIELDS = (
    "interest_rate",
    "inflation",
    "gdp_growth",
    "unemployment",
    "tax",
)


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _resolve_locale() -> str:
    """Dashboard locale (env DASHBOARD_DEFAULT_LOCALE always wins)."""
    from core.locale_prefs import dashboard_locale

    return dashboard_locale()


def _macro_slice(state: dict[str, Any], subjects: list[str]) -> dict[str, Any]:
    countries = state.get("countries") or {}
    # Empty subjects => measure the whole live world (global spillover).
    codes = subjects or list(countries.keys())
    out: dict[str, Any] = {"sim_day": state.get("sim_day"), "countries": {}}
    for code in codes[:24]:
        row = countries.get(code)
        if not isinstance(row, dict):
            continue
        slim: dict[str, float] = {}
        for field in _TRACK_FIELDS:
            val = row.get(field)
            if isinstance(val, (int, float)):
                slim[field] = float(val)
            elif field == "inflation":
                mon = (row.get("vectors") or {}).get("monetary") or {}
                if isinstance(mon.get("inflation_cpi"), (int, float)):
                    slim[field] = float(mon["inflation_cpi"])
            elif field == "tax":
                fis = (row.get("vectors") or {}).get("fiscal") or {}
                if isinstance(fis.get("corporate_tax"), (int, float)):
                    slim[field] = float(fis["corporate_tax"])
            elif field == "interest_rate":
                mon = (row.get("vectors") or {}).get("monetary") or {}
                if isinstance(mon.get("interest_rate"), (int, float)):
                    slim[field] = float(mon["interest_rate"])
            elif field == "unemployment":
                lab = (row.get("vectors") or {}).get("labor") or {}
                if isinstance(lab.get("unemployment"), (int, float)):
                    slim[field] = float(lab["unemployment"])
        out["countries"][code] = slim
    return out


def _subjects_from_deltas(
    mutations: list[dict[str, Any]],
    deltas: dict[str, float],
    *,
    fallback: list[str] | None = None,
    limit: int = 5,
) -> list[str]:
    """Label the cycle by mutation origins + strongest moved hubs."""
    codes: list[str] = []
    for m in mutations or []:
        if isinstance(m, dict) and m.get("country"):
            c = str(m["country"]).upper()
            if c not in codes:
                codes.append(c)
    ranked = sorted(
        deltas.items(),
        key=lambda kv: abs(float(kv[1])),
        reverse=True,
    )
    for key, _val in ranked:
        if "." not in key:
            continue
        c = key.split(".", 1)[0].upper()
        if c not in codes:
            codes.append(c)
        if len(codes) >= limit:
            break
    if not codes and fallback:
        codes = list(fallback)[:limit]
    return codes[:limit]


def _deltas(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, float]:
    deltas: dict[str, float] = {}
    pre_c = pre.get("countries") or {}
    post_c = post.get("countries") or {}
    for code, post_row in post_c.items():
        pre_row = pre_c.get(code) or {}
        if not isinstance(post_row, dict):
            continue
        for field, post_val in post_row.items():
            if not isinstance(post_val, (int, float)):
                continue
            pre_val = pre_row.get(field)
            if not isinstance(pre_val, (int, float)):
                continue
            deltas[f"{code}.{field}"] = round(float(post_val) - float(pre_val), 6)
    return deltas


class HypothesisRunner:
    """Armed only when SystemController permits autonomous hypothesis + World on."""

    def __init__(
        self,
        bus: EventBus,
        kernel: WorldKernel,
        scenario: LLMScenarioEngine,
        *,
        controller: SystemController | None = None,
        generator: HypothesisGenerator | None = None,
        rollout_writer: SealedRolloutWriter | None = None,
        interval_sec: float | None = None,
        settle_sec: float | None = None,
        history_maxlen: int | None = None,
        feature_snapshot: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._bus = bus
        self._kernel = kernel
        self._scenario = scenario
        self._controller = controller or get_system_controller()
        self._generator = generator or HypothesisGenerator(kernel)
        self._thinker = ScientificHypothesisThinker(self._generator)
        self._rollouts = rollout_writer or SealedRolloutWriter()
        self._interval = (
            interval_sec
            if interval_sec is not None
            else _env_float("HYPOTHESIS_INTERVAL_SEC", 45.0)
        )
        self._settle = (
            settle_sec
            if settle_sec is not None
            else _env_float("HYPOTHESIS_SETTLE_SEC", 2.0)
        )
        self._maxlen = (
            history_maxlen
            if history_maxlen is not None
            else _env_int("HYPOTHESIS_HISTORY_MAX", 50)
        )
        self._feature_snapshot = feature_snapshot
        self._report = HypothesisReport()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    @property
    def report(self) -> HypothesisReport:
        return self._report

    def report_dict(self) -> dict[str, Any]:
        return self._report.as_dict()

    def is_armed(self) -> bool:
        return (
            self._controller.is_autonomous_hypothesis()
            and self._controller.world_simulation_enabled
        )

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="hypothesis-runner")
        logger.info(
            "hypothesis runner started (interval=%.0fs settle=%.1fs)",
            self._interval,
            self._settle,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("hypothesis runner stopped")

    async def run_once(self, *, force: bool = False) -> HypothesisOutcome:
        """Execute one generate→scenario→settle→measure cycle."""
        async with self._lock:
            if not force and not self.is_armed():
                hyp = Hypothesis(
                    id="skipped",
                    prompt="",
                    rationale="runner not armed",
                )
                outcome = HypothesisOutcome(
                    hypothesis_id=hyp.id,
                    status="skipped",
                    error="autonomous hypothesis disarmed or world simulation off",
                )
                self._report.append(hyp, outcome, maxlen=self._maxlen)
                return outcome

            hyp = await self._thinker.propose()
            # LLM debates in English (more reliable); Vietnamese is attached at publish.
            try:
                hyp = await debate_hypothesis(
                    self._thinker, hyp, locale="en"
                )
            except Exception:  # noqa: BLE001 — debate must never block research
                logger.exception("hypothesis debate failed; using raw proposal")
            pre_day = int(getattr(self._kernel.world, "sim_day", 0) or 0)
            # Measure ALL live hubs so spillover outside the named pair is visible.
            pre = _macro_slice(self._kernel.state_dict(), [])

            # Prefer explicit indicator mutations from the scientific thinker.
            applied_mutations: list[str] = []
            mutations = list(hyp.mutations or [])
            if mutations:
                try:
                    applied_mutations = self._kernel.apply_mutations(mutations)
                except Exception:  # noqa: BLE001
                    logger.exception("hypothesis direct mutations failed")

            try:
                # Still run NL scenario for micro coupling / parser path;
                # when mutations already applied, announce=False keeps logs quieter.
                scenario_result = await self._scenario.run_scenario(
                    hyp.prompt, announce=False
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("hypothesis scenario failed")
                outcome = HypothesisOutcome(
                    hypothesis_id=hyp.id,
                    status="error",
                    pre_macro=pre,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._report.append(hyp, outcome, maxlen=self._maxlen)
                await self._publish(hyp, outcome, applied_mutations=applied_mutations)
                return outcome

            if isinstance(scenario_result, dict) and scenario_result.get("error"):
                # Direct mutations may still have succeeded — treat as ok if so.
                if applied_mutations:
                    scenario_result = {
                        **scenario_result,
                        "direct_mutations": applied_mutations,
                        "scenario_warning": str(scenario_result.get("error")),
                    }
                else:
                    outcome = HypothesisOutcome(
                        hypothesis_id=hyp.id,
                        status="error",
                        pre_macro=pre,
                        scenario=scenario_result,
                        error=str(scenario_result.get("error")),
                    )
                    self._report.append(hyp, outcome, maxlen=self._maxlen)
                    await self._publish(hyp, outcome, applied_mutations=applied_mutations)
                    return outcome

            if self._settle > 0:
                await asyncio.sleep(self._settle)

            post_day = int(getattr(self._kernel.world, "sim_day", 0) or 0)
            post = _macro_slice(self._kernel.state_dict(), [])
            micro = {}
            if isinstance(scenario_result, dict):
                micro = scenario_result.get("micro_impact") or {}
                if applied_mutations:
                    scenario_result = {
                        **scenario_result,
                        "direct_mutations": applied_mutations,
                    }
            deltas = _deltas(pre, post)
            # Relabel subjects from actual movers (not a stale USA+CHN pair).
            hyp = hyp.model_copy(
                update={
                    "subjects": _subjects_from_deltas(
                        mutations, deltas, fallback=list(hyp.subjects or [])
                    )
                }
            )
            outcome = HypothesisOutcome(
                hypothesis_id=hyp.id,
                status="ok",
                tick_span=max(0, post_day - pre_day),
                settle_sec=self._settle,
                pre_macro=pre,
                post_macro=post,
                deltas=deltas,
                micro_summary=micro if isinstance(micro, dict) else {},
                scenario=scenario_result if isinstance(scenario_result, dict) else {},
            )
            self._report.append(hyp, outcome, maxlen=self._maxlen)
            await self._publish(hyp, outcome, applied_mutations=applied_mutations)
            self._export_rollout(hyp, outcome)
            return outcome

    async def _loop(self) -> None:
        while self._running:
            try:
                if self.is_armed():
                    await self.run_once(force=False)
                await asyncio.sleep(max(5.0, self._interval))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("hypothesis runner loop error")
                await asyncio.sleep(max(5.0, self._interval))

    async def _publish(
        self,
        hyp: Hypothesis,
        outcome: HypothesisOutcome,
        *,
        applied_mutations: list[str] | None = None,
    ) -> None:
        payload = {
            "hypothesis_id": hyp.id,
            "prompt": hyp.prompt,
            "status": outcome.status,
            "category": hyp.category,
            "severity": hyp.severity,
            "subjects": hyp.subjects,
            "generator": hyp.generator,
            "mutations": hyp.mutations,
            "applied_mutations": applied_mutations or [],
            "deltas": outcome.deltas,
            "error": outcome.error,
            "rationale": hyp.rationale,
            "question": hyp.question,
            "chapter": hyp.chapter,
            "fingerprint": hyp.fingerprint,
            "independent_vars": hyp.independent_vars,
            "dependent_vars": hyp.dependent_vars,
            "expected": hyp.expected,
            "chair_decision": hyp.chair_decision,
            "chair_reason": hyp.chair_reason,
            "debate": [t.model_dump() for t in hyp.debate],
            "signal_score": signal_score(outcome.deltas or {}),
        }
        await self._bus.publish("world.hypothesis", **payload)
        from ai.simulator_engine.log_i18n import format_hypothesis_log

        locale = _resolve_locale()
        msg = format_hypothesis_log(hyp, outcome, locale)
        # Prefer LLM rewrite into dashboard language (no extra VI templates).
        llm_pool = getattr(self._generator, "_llm_pool", None)
        if llm_pool is not None and outcome.status != "skipped":
            ask = ""
            for line in (hyp.rationale or "").splitlines():
                if line.startswith("ASK:"):
                    ask = line[4:].strip()
                    break
            top = ""
            if outcome.deltas:
                items = sorted(
                    outcome.deltas.items(),
                    key=lambda kv: abs(float(kv[1])),
                    reverse=True,
                )[:3]
                top = "; ".join(
                    f"{k} {'+' if float(v) >= 0 else ''}{float(v):.4f}" for k, v in items
                )
            from ai.simulator_engine.operator_voice import compose_event_from_facts

            llm_msg = await asyncio.to_thread(
                compose_event_from_facts,
                llm_pool,
                base_url=getattr(self._generator, "_llm_base_url", "") or "",
                model=getattr(self._generator, "_llm_model", "") or "",
                locale=locale,
                facts={
                    "kind": "hypothesis_result",
                    "id": hyp.id[:8],
                    "question": ask or hyp.prompt,
                    "status": outcome.status,
                    "mutations": hyp.mutations,
                    "top_deltas": top,
                    "error": outcome.error or None,
                },
            )
            if llm_msg:
                msg = llm_msg
        if outcome.status == "ok":
            level = "warn" if hyp.severity > 0.7 else "info"
        elif outcome.status == "skipped":
            level = "info"
        else:
            level = "danger"
        await self._bus.publish(
            "system.log", level=level, source="hypothesis", message=msg
        )

        # Scientific dialogue on Agents tab: debate → ask → act → judge.
        if outcome.status == "skipped":
            return

        await self._publish_lines(
            debate_dialogue_lines(hyp),
            level=level,
            locale=locale,
            hyp=hyp,
        )

        reflection = await self._thinker.reflect(hyp, outcome)
        payload["assessment"] = reflection.get("assessment", "")
        payload["next_question"] = reflection.get("next_question", "")
        await self._bus.publish("world.hypothesis.reflection", **payload)

        # Compose ask/act/judge in English; VI is produced at publish for the UI.
        await self._publish_lines(
            await compose_dialogue_lines(
                self._thinker, hyp, outcome, reflection, locale="en"
            ),
            level=level,
            locale=locale,
            hyp=hyp,
            reflection=reflection,
        )

    async def _publish_lines(
        self,
        lines: list[dict[str, str]],
        *,
        level: str,
        locale: str,
        hyp: Hypothesis | None = None,
        reflection: dict[str, Any] | None = None,
    ) -> None:
        """Publish English LLM prose plus a Vietnamese display string for the UI.

        The model always speaks English (full debate volume). Dashboard locale
        only selects which field to show — never drops lines.
        """
        from ai.simulator_engine.hypothesis_i18n import vi_fallback_for_phase
        from ai.simulator_engine.operator_voice import (
            ensure_locale_text,
            is_operator_locale,
        )

        llm_pool = getattr(self._generator, "_llm_pool", None)
        base_url = getattr(self._generator, "_llm_base_url", "") or ""
        model = getattr(self._generator, "_llm_model", "") or ""
        for line in lines:
            phase = line.get("phase", "think")
            text_en = (line.get("text") or "").strip()
            if not text_en:
                continue
            fallback = (
                vi_fallback_for_phase(phase, hyp, reflection=reflection)
                if hyp is not None
                else ""
            )
            text_vi = fallback
            if llm_pool is not None:
                translated = await asyncio.to_thread(
                    ensure_locale_text,
                    llm_pool,
                    base_url=base_url,
                    model=model,
                    locale="vi",
                    text=text_en,
                    context=phase,
                    fallback=fallback or None,
                )
                if translated and is_operator_locale(translated, "vi"):
                    text_vi = translated
                elif fallback:
                    text_vi = fallback
            elif not text_vi and is_operator_locale(text_en, "vi"):
                text_vi = text_en
            source = {
                "Corporate AI": "corporate",
                "Government AI": "government",
                "Societal AI": "society",
                "Central Bank": "government",
                "Chair": "government",
            }.get(line["actor"], "sovereign")
            await self._bus.publish(
                "world.agent.narrative",
                sim_day=getattr(self._kernel.world, "sim_day", None),
                actor=line["actor"],
                country=line["country"],
                text=text_en,
                text_vi=text_vi,
                level=level,
                source=source,
                # Source language of ``text`` is English; UI picks text_vi when needed.
                locale="en",
                provenance=f"hypothesis:{phase}",
            )

    def _export_rollout(self, hyp: Hypothesis, outcome: HypothesisOutcome) -> None:
        try:
            features: dict[str, Any] = {}
            if self._feature_snapshot is not None:
                features = dict(self._feature_snapshot() or {})
            self._rollouts.write(
                hypothesis_id=hyp.id,
                prompt=hyp.prompt,
                outcome=outcome,
                features=features,
                world_coupling=float(
                    (outcome.micro_summary or {}).get("volatility_multiplier", 0.0) or 0.0
                ),
                mutations=list(hyp.mutations or []),
                signal_score=signal_score(outcome.deltas or {}),
                fingerprint=hyp.fingerprint,
                chapter=hyp.chapter,
                question=hyp.question,
                chair_decision=hyp.chair_decision,
                debate_summary=debate_summary(hyp),
            )
        except Exception:  # noqa: BLE001 — export must never break the runner
            logger.exception("sealed rollout export failed")
