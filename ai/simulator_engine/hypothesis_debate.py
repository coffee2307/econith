"""ECONITH :: pre-mutation debate between world agents.

Before a hypothesis is applied to the World, three desks argue about it and a
chair decides:

  * Government AI — proposes / defends the experiment
  * Corporate AI  — challenges feasibility and market risk
  * Societal AI   — states the social cost
  * Chair         — ``accept`` | ``revise`` (soften mutations) | ``pivot``

One LLM call produces the whole transcript. When the LLM is unavailable or
returns junk, a deterministic heuristic transcript is used so the demo never
loses its Agents-tab dialogue.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ai.simulator_engine.hypothesis_schema import DebateTurn, Hypothesis
from ai.simulator_engine.operator_voice import language_name

logger = logging.getLogger("econith.world.hypothesis_debate")

_DECISIONS = ("accept", "revise", "pivot")

# How much a "revise" decision softens each mutation toward its current level.
_REVISE_DAMPING = 0.5


def _clean_text(value: Any, *, limit: int = 320) -> str:
    return str(value or "").strip()[:limit]


def _current_value(snapshot: dict[str, Any], country: str, field: str) -> float | None:
    from ai.simulator_engine.hypothesis_generator import _field_value

    return _field_value(snapshot, country, field)


def soften_mutations(
    mutations: list[dict[str, Any]],
    snapshot: dict[str, Any],
    *,
    damping: float = _REVISE_DAMPING,
) -> list[dict[str, Any]]:
    """Pull each target value halfway back toward the country's current level."""
    out: list[dict[str, Any]] = []
    for m in mutations or []:
        if not isinstance(m, dict):
            continue
        country = str(m.get("country") or "")
        field = str(m.get("field") or "")
        try:
            target = float(m.get("value"))
        except (TypeError, ValueError):
            continue
        current = _current_value(snapshot, country, field)
        if current is None:
            out.append(dict(m))
            continue
        softened = current + (target - current) * damping
        out.append({**m, "value": round(softened, 6)})
    return out


def _heuristic_debate(hyp: Hypothesis, locale: str) -> tuple[list[DebateTurn], str, str]:
    vi = (locale or "en").lower().startswith("vi")
    plan = ", ".join(
        f"{m.get('country')}.{m.get('field')}→{m.get('value')}"
        for m in (hyp.mutations or [])[:2]
    ) or hyp.prompt
    if vi:
        turns = [
            DebateTurn(
                actor="Government AI",
                stance="propose",
                text=f"Chính phủ đề xuất thí nghiệm: {plan}. Mục tiêu là đo phản ứng vĩ mô.",
            ),
            DebateTurn(
                actor="Corporate AI",
                stance="challenge",
                text=(
                    "Doanh nghiệp lo ngại chi phí vốn và biên lợi nhuận biến động "
                    f"khi cường độ ở mức {hyp.severity:.2f}."
                ),
            ),
            DebateTurn(
                actor="Societal AI",
                stance="cost",
                text="Xã hội cảnh báo áp lực lên việc làm và giá tiêu dùng nếu cú sốc quá mạnh.",
            ),
        ]
        reason = "Chủ tọa chấp thuận nhưng giữ bước điều chỉnh nhỏ để kiểm soát rủi ro."
    else:
        turns = [
            DebateTurn(
                actor="Government AI",
                stance="propose",
                text=f"Government proposes the experiment: {plan}, to measure macro response.",
            ),
            DebateTurn(
                actor="Corporate AI",
                stance="challenge",
                text=(
                    "Corporate desk flags funding-cost and margin volatility at "
                    f"severity {hyp.severity:.2f}."
                ),
            ),
            DebateTurn(
                actor="Societal AI",
                stance="cost",
                text="Society warns about employment and consumer-price pressure if the shock overshoots.",
            ),
        ]
        reason = "Chair accepts but keeps the step small to contain risk."
    decision = "revise" if hyp.severity > 0.7 else "accept"
    turns.append(DebateTurn(actor="Chair", stance=decision, text=reason))
    return turns, decision, reason


def _debate_llm(
    llm_pool: Any,
    *,
    base_url: str,
    model: str,
    hyp: Hypothesis,
    locale: str,
    repeat_warning: str,
) -> tuple[list[DebateTurn], str, str] | None:
    from ai.simulator_engine.hypothesis_thinker import _extract_json
    from ai.simulator_engine.operator_voice import ensure_locale_text

    lang = language_name(locale)
    plan = [
        {"country": m.get("country"), "field": m.get("field"), "value": m.get("value")}
        for m in (hyp.mutations or [])[:2]
    ]
    messages = [
        {
            "role": "system",
            "content": (
                "You moderate a short policy debate inside an economic simulator. "
                "Three desks argue about ONE proposed experiment, then a chair rules. "
                "Reply ONLY valid JSON:\n"
                "{\n"
                '  "government": "1 sentence supporting the experiment",\n'
                '  "corporate": "1 sentence challenging feasibility / market risk",\n'
                '  "society": "1 sentence on the social cost",\n'
                '  "decision": "accept|revise|pivot",\n'
                '  "chair": "1 sentence ruling with the reason"\n'
                "}\n"
                f"CRITICAL: every string MUST be written in {lang} "
                f"(not English if {lang} is Vietnamese). "
                "Keep ISO codes and numbers. "
                "Use 'revise' when the shock is too aggressive, 'pivot' when the "
                "experiment repeats an earlier one, otherwise 'accept'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language required: {lang}\n"
                f"Question: {hyp.question or hyp.prompt}\n"
                f"Proposed mutations: {plan}\n"
                f"Independent vars: {hyp.independent_vars}\n"
                f"Dependent vars: {hyp.dependent_vars}\n"
                f"Expected: {hyp.expected}\n"
                f"Severity: {hyp.severity:.2f}\n"
                f"Chapter: {hyp.chapter or 'n/a'}\n"
                f"Repeat check: {repeat_warning}"
            ),
        },
    ]
    resp = llm_pool.create_chat_completion(
        base_url=base_url or "http://localhost:11434/v1",
        model=model or "llama3:8b",
        timeout=60.0,
        messages=messages,
        max_tokens=340,
        temperature=0.6,
    )
    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        content = str(resp)
    data = _extract_json(content)
    if not data:
        return None
    gov = _clean_text(data.get("government"))
    corp = _clean_text(data.get("corporate"))
    soc = _clean_text(data.get("society"))
    chair = _clean_text(data.get("chair"))
    if not (gov and corp and soc and chair):
        return None
    # Force dashboard language — llama3 often ignores the language instruction.
    from ai.simulator_engine.hypothesis_i18n import vi_debate_line
    from ai.simulator_engine.operator_voice import is_operator_locale

    vi = (locale or "").lower().startswith("vi")
    gov = ensure_locale_text(
        llm_pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=gov,
        context="gov",
        fallback=vi_debate_line(hyp, "Government AI") if vi else None,
    )
    corp = ensure_locale_text(
        llm_pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=corp,
        context="corp",
        fallback=vi_debate_line(hyp, "Corporate AI") if vi else None,
    )
    soc = ensure_locale_text(
        llm_pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=soc,
        context="society",
        fallback=vi_debate_line(hyp, "Societal AI") if vi else None,
    )
    chair = ensure_locale_text(
        llm_pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=chair,
        context="chair",
        fallback=vi_debate_line(hyp, "Chair") if vi else None,
    )
    # If translation still failed, use Vietnamese heuristic lines (keep LLM decision).
    if vi and not all(is_operator_locale(t, "vi") for t in (gov, corp, soc, chair)):
        h_turns, _h_dec, h_reason = _heuristic_debate(hyp, locale)
        by_actor = {t.actor: t.text for t in h_turns}
        gov = by_actor.get("Government AI", gov)
        corp = by_actor.get("Corporate AI", corp)
        soc = by_actor.get("Societal AI", soc)
        chair = by_actor.get("Chair", chair) or h_reason
    decision = str(data.get("decision") or "accept").strip().lower()
    if decision not in _DECISIONS:
        decision = "accept"
    turns = [
        DebateTurn(actor="Government AI", stance="propose", text=gov),
        DebateTurn(actor="Corporate AI", stance="challenge", text=corp),
        DebateTurn(actor="Societal AI", stance="cost", text=soc),
        DebateTurn(actor="Chair", stance=decision, text=chair),
    ]
    return turns, decision, chair


async def debate_hypothesis(
    thinker: Any,
    hyp: Hypothesis,
    *,
    locale: str = "en",
) -> Hypothesis:
    """Run the pre-mutation debate and return the hypothesis the chair approved."""
    base = getattr(thinker, "_base", None)
    memory = getattr(thinker, "memory", None)
    repeated = bool(
        hyp.fingerprint
        and memory is not None
        and hyp.fingerprint in memory.avoid_fingerprints(n=3)
    )
    repeat_warning = (
        f"This change-signature was already tested recently: {hyp.fingerprint}"
        if repeated
        else "No recent duplicate detected."
    )

    result: tuple[list[DebateTurn], str, str] | None = None
    if base is not None and getattr(base, "_use_llm", False) and getattr(base, "_llm_pool", None):
        try:
            result = await asyncio.to_thread(
                _debate_llm,
                base._llm_pool,
                base_url=base._llm_base_url or "",
                model=base._llm_model or "",
                hyp=hyp,
                locale=locale,
                repeat_warning=repeat_warning,
            )
        except Exception:  # noqa: BLE001
            logger.warning("hypothesis debate LLM failed; heuristic", exc_info=True)
    if result is None:
        result = _heuristic_debate(hyp, locale)

    turns, decision, reason = result
    if repeated and decision == "accept":
        decision = "pivot"

    mutations = list(hyp.mutations or [])
    snapshot: dict[str, Any] = {}
    if base is not None:
        try:
            snapshot = base._kernel.state_dict()
        except Exception:  # noqa: BLE001
            snapshot = {}

    if decision == "revise" and mutations:
        mutations = soften_mutations(mutations, snapshot)
    elif decision == "pivot" and mutations:
        # Keep the experiment but halve it and mark the pivot for the next cycle.
        mutations = soften_mutations(mutations, snapshot, damping=0.35)

    from ai.simulator_engine.hypothesis_thinker import mutation_fingerprint

    return hyp.model_copy(
        update={
            "mutations": mutations,
            "fingerprint": mutation_fingerprint(mutations),
            "debate": turns,
            "chair_decision": decision,
            "chair_reason": reason,
        }
    )


def debate_dialogue_lines(hyp: Hypothesis) -> list[dict[str, str]]:
    """Agents-tab lines for the debate phase (before ask/act/judge)."""
    country = "+".join(hyp.subjects[:3]) if hyp.subjects else "GLOBAL"
    phase_by_actor = {
        "Government AI": "debate:gov",
        "Corporate AI": "debate:corp",
        "Societal AI": "debate:society",
        "Chair": "debate:chair",
    }
    lines: list[dict[str, str]] = []
    for turn in hyp.debate:
        if not turn.text:
            continue
        lines.append(
            {
                "actor": turn.actor,
                "country": country,
                "text": turn.text,
                "phase": phase_by_actor.get(turn.actor, "debate"),
            }
        )
    return lines


def debate_summary(hyp: Hypothesis) -> str:
    """Compact one-line record of the debate for sealed rollouts."""
    if not hyp.debate:
        return ""
    bits = [f"{t.actor}[{t.stance}]: {t.text[:120]}" for t in hyp.debate]
    return " | ".join(bits)[:800]
