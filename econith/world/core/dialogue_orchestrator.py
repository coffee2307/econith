"""ECONITH :: econith.world.core.dialogue_orchestrator

Material-triggered multi-agent deliberation.

Physics runs every tick. When emergent events cross material thresholds a
dynamic cast (Labor / Corporate / Household / Central Bank / Government for the
affected countries) deliberates. Agents must answer prior speakers; only
validated policy-role decisions become short-lived GovernorDirective overlays.
Numbers in prose must come from grounded physics metrics.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from typing import Any, Callable

from econith.world.agents.state_arrays import EmergentEvent
from econith.world.core.dialogue_schema import (
    ACTION_ENUMS,
    DialogueTurnBundle,
    GroundedMetric,
)
from econith.world.core.dialogue_validator import (
    build_cast,
    build_fallback_bundle,
    decisions_to_directives,
    parse_dialogue_json,
)
from econith.world.core.hierarchy_broker import GovernorDirective

logger = logging.getLogger("econith.world.dialogue")

__all__ = ["DialogueOrchestrator", "is_material"]

_MATERIAL_KINDS = frozenset({
    "labor_strike",
    "safe_haven_migration",
    "demand_contraction",
    "demand_expansion",
})
_MIN_INTENSITY = 0.55


def is_material(events: list[EmergentEvent], *, min_intensity: float = _MIN_INTENSITY) -> tuple[bool, str]:
    hits = [
        e for e in events
        if e.kind in _MATERIAL_KINDS and float(e.intensity) >= min_intensity
    ]
    if not hits:
        return False, ""
    top = max(hits, key=lambda e: e.intensity)
    return True, f"{top.kind}@{top.node}:{top.intensity:.2f}"


class DialogueOrchestrator:
    """Async multi-agent deliberation with memory and single-flight backpressure."""

    def __init__(
        self,
        *,
        valid_codes: tuple[str, ...] | list[str],
        pool: Any = None,
        base_url: str = "",
        model: str = "",
        on_directives: Callable[[dict[str, GovernorDirective]], None] | None = None,
        cooldown_ticks: int = 15,
        memory_episodes: int = 3,
    ) -> None:
        self._codes = tuple(valid_codes)
        self._valid = set(self._codes)
        self._pool = pool
        self._base_url = base_url
        self._model = model
        self._on_directives = on_directives
        self._cooldown = max(1, int(cooldown_ticks))
        self._task: asyncio.Task | None = None
        self._last_tick = -10_000
        self._last_bundle: DialogueTurnBundle | None = None
        self._rejected_total = 0
        self._memory: deque[dict[str, Any]] = deque(maxlen=max(1, int(memory_episodes)))

    @property
    def in_flight(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def last_bundle(self) -> DialogueTurnBundle | None:
        return self._last_bundle

    @property
    def rejected_total(self) -> int:
        return self._rejected_total

    def maybe_schedule(
        self,
        *,
        tick: int,
        events: list[EmergentEvent],
        macro: dict[str, dict[str, float]],
        locale: str = "en",
    ) -> DialogueTurnBundle | None:
        """If material and not cooling down / in-flight, start async deliberation.

        Returns a status-only fallback immediately (no hardcode policy). When the
        LLM finishes, the last_bundle is upgraded to a real multi-turn debate and
        only then may directives be applied.
        """
        ok, reason = is_material(events)
        if not ok:
            return None
        if tick - self._last_tick < self._cooldown:
            return self._last_bundle
        self._last_tick = tick

        fallback = build_fallback_bundle(
            tick=tick, events=events, locale=locale, material_reason=reason,
        )
        # Status only — do NOT push hardcode tighten/ease into physics.
        self._last_bundle = fallback

        if not self._pool:
            return fallback
        if self.in_flight:
            return fallback

        grounded = self._ground_metrics(events, tick, macro)
        cast = build_cast(events, locale=locale)
        self._task = asyncio.create_task(
            self._deliberate(
                tick=tick,
                events=events,
                macro=macro,
                grounded=grounded,
                cast=cast,
                locale=locale,
                material_reason=reason,
            ),
            name=f"world-dialogue-{tick}",
        )
        return fallback

    async def _deliberate(
        self,
        *,
        tick: int,
        events: list[EmergentEvent],
        macro: dict[str, dict[str, float]],
        grounded: list[GroundedMetric],
        cast: list,
        locale: str,
        material_reason: str,
    ) -> None:
        prompt = self._build_prompt(
            events, macro, grounded, cast=cast, locale=locale,
        )
        try:
            raw = await asyncio.to_thread(self._call_llm, prompt)
        except Exception:  # noqa: BLE001
            logger.exception("dialogue LLM failed; keeping status bundle")
            return
        decisions, utterances, rejected = parse_dialogue_json(
            raw,
            valid_codes=self._valid,
            grounded=grounded,
            tick=tick,
            locale=locale,
            cast=cast,
        )
        self._rejected_total += rejected
        if not utterances and not decisions:
            return

        # Prefer utterances that form a response chain; keep decisions for policy.
        bundle = DialogueTurnBundle(
            tick=tick,
            decisions=decisions,
            utterances=utterances,
            source="llm" if decisions or utterances else "hybrid",
            rejected=rejected,
            material_reason=material_reason,
            level="warn",
            cast=[p.as_dict() for p in cast],
        )
        dirs = decisions_to_directives(decisions, cast=cast)
        if self._on_directives and dirs:
            self._on_directives(dirs)
        self._last_bundle = bundle
        self._memory.append({
            "tick": tick,
            "reason": material_reason,
            "lines": [
                {
                    "agent_id": u.agent_id,
                    "role": u.role,
                    "country": u.country,
                    "text": u.text,
                    "responds_to": u.responds_to,
                    "action": next(
                        (d.action_id for d in decisions if d.agent_id == u.agent_id),
                        "",
                    ),
                }
                for u in utterances[:6]
            ],
        })
        logger.info(
            "dialogue turn tick=%s source=%s speakers=%d decisions=%d rejected=%d reason=%s",
            tick, bundle.source, len(utterances), len(decisions), rejected, material_reason,
        )

    def _call_llm(self, prompt: str) -> str:
        response = self._pool.create_chat_completion(
            base_url=self._base_url,
            model=self._model,
            timeout=35.0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You simulate a multi-agent economic debate. Each agent has "
                        "a distinct objective and must REPLY to the previous speaker "
                        "(agree, disagree, or counter-propose). Output strict JSON only. "
                        "Never invent numeric magnitudes — only use numbers from "
                        "grounded_metrics. Pick action_id only from each agent's "
                        "allowed_actions. Keep the scenario realistic and proportional "
                        "to the event intensity. Do not write generic filler about "
                        "global CPI oscillating."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.55,
            max_tokens=1100,
        )
        return (response.choices[0].message.content or "").strip()

    def _build_prompt(
        self,
        events: list[EmergentEvent],
        macro: dict[str, dict[str, float]],
        grounded: list[GroundedMetric],
        *,
        cast: list,
        locale: str,
    ) -> str:
        lang = "Vietnamese" if locale.startswith("vi") else "English"
        cast_codes = {p.country for p in cast}
        brief = {
            "language": lang,
            "protocol": [
                "Speak in cast order.",
                "Turn 0 may open the issue; every later turn MUST set responds_to to a prior agent_id and address their claim.",
                "Disagreement is required at least once.",
                "Only policy roles (Central Bank / Government) may choose ease/tighten/qe_pulse/tax_*/tariff_*.",
                "Labor/Corporate/Household may only hold / bargain_accept / bargain_resist.",
                "Every turn needs grounding event_id from grounded_metrics.",
                "Utterance numbers MUST come from grounded_metrics only.",
            ],
            "action_allowlist": sorted(ACTION_ENUMS),
            "cast": [p.as_dict() for p in cast],
            "grounded_metrics": [m.as_dict() for m in grounded[:24]],
            "events": [
                {
                    "node": e.node,
                    "kind": e.kind,
                    "intensity": round(float(e.intensity), 3),
                    "metrics": {
                        k: round(float(v), 4)
                        for k, v in (e.metrics or {}).items()
                    },
                }
                for e in events[:8]
            ],
            "macro": {
                c: {
                    "interest_rate": round(macro[c].get("interest_rate", 0.03), 4),
                    "inflation": round(macro[c].get("inflation_cpi", 0.025), 4),
                    "gdp_growth": round(macro[c].get("gdp_growth", 0.02), 4),
                    "unemployment": round(macro[c].get("unemployment", 0.05), 4),
                }
                for c in cast_codes
                if c in macro
            },
            "memory": list(self._memory),
        }
        return (
            "Run a causal multi-agent deliberation about the material events. "
            "Return JSON of the form "
            '{"turns":[{"agent_id":str,"role":str,"country":str,"text":str,'
            '"responds_to":str|null,"action_id":str,"grounding":[str]}]}. '
            f"Produce exactly {min(3, len(cast))} turns using the first cast "
            "members in order. "
            "Each speaker must advance the scenario (propose next step or "
                "counter the previous agent), not repeat slogans. Each text must "
                "be one sentence and <= 35 words; rationale <= 8 words.\n\n"
            + json.dumps(brief, ensure_ascii=False)
        )

    @staticmethod
    def _ground_metrics(
        events: list[EmergentEvent],
        tick: int,
        macro: dict[str, dict[str, float]],
    ) -> list[GroundedMetric]:
        out: list[GroundedMetric] = []
        for e in events[:8]:
            eid = f"{e.node}:{e.kind}"
            out.append(GroundedMetric("intensity", float(e.intensity), "", tick, eid))
            for k, v in list((e.metrics or {}).items())[:5]:
                try:
                    out.append(GroundedMetric(str(k), float(v), "", tick, eid))
                except (TypeError, ValueError):
                    continue
            snap = macro.get(e.node, {})
            for name, key, unit in (
                ("interest_rate", "interest_rate", ""),
                ("inflation_cpi", "inflation_cpi", ""),
                ("gdp_growth", "gdp_growth", ""),
                ("unemployment", "unemployment", ""),
            ):
                if key in snap:
                    try:
                        out.append(
                            GroundedMetric(name, float(snap[key]), unit, tick, eid)
                        )
                    except (TypeError, ValueError):
                        continue
        return out
