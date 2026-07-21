"""ECONITH :: econith.world.core.dialogue_validator

Parse / clamp / reject dialogue payloads. Physics never sees raw LLM floats —
only :class:`GovernorDirective`-compatible deltas derived from allowlisted
action enums. Prose that invents digits not present in the grounded metric set
is stripped / rejected. Decisions without event grounding are rejected.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from econith.world.core.dialogue_schema import (
    ACTION_ENUMS,
    ACTION_TO_DELTAS,
    POLICY_ROLES,
    AgentPersona,
    DialogueDecision,
    DialogueTurnBundle,
    DialogueUtterance,
    GroundedMetric,
    actions_for_role,
)
from econith.world.core.hierarchy_broker import GovernorDirective

logger = logging.getLogger("econith.world.dialogue.validator")

__all__ = [
    "strip_ungrounded_numbers",
    "parse_dialogue_json",
    "decisions_to_directives",
    "build_fallback_bundle",
    "build_cast",
]

_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])[-+]?\d+(?:[.,]\d+)?%?(?![A-Za-z])"
)


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def strip_ungrounded_numbers(text: str, metrics: list[GroundedMetric]) -> str:
    """Remove numeric tokens from ``text`` that are not in ``metrics``."""
    allowed: set[str] = set()
    for m in metrics:
        allowed.add(f"{m.value:.0f}")
        allowed.add(f"{m.value:.1f}")
        allowed.add(f"{m.value:.2f}")
        allowed.add(f"{m.value * 100:.0f}")
        allowed.add(f"{m.value * 100:.1f}")
        allowed.add(f"{m.value:.1f}".replace(".", ","))
        allowed.add(f"{m.value * 100:.1f}".replace(".", ","))

    def _keep(match: re.Match[str]) -> str:
        token = match.group(0).rstrip("%")
        bare = token.replace(",", ".")
        if token in allowed or bare in allowed:
            return match.group(0)
        try:
            if abs(float(bare)) <= 4 and "." not in bare and "," not in bare:
                return match.group(0)
        except ValueError:
            pass
        return ""

    cleaned = _NUMBER_RE.sub(_keep, text)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,;:-")


def build_cast(
    events: list[Any],
    *,
    locale: str = "en",
    max_countries: int = 2,
) -> list[AgentPersona]:
    """Build a dynamic cast from material events — no fixed country list."""
    vi = locale.startswith("vi")
    ranked = sorted(
        events,
        key=lambda e: float(getattr(e, "intensity", 0.0) or 0.0),
        reverse=True,
    )
    countries: list[str] = []
    for e in ranked:
        node = str(getattr(e, "node", "") or "").upper()
        if node and node not in countries:
            countries.append(node)
        if len(countries) >= max_countries:
            break
    cast: list[AgentPersona] = []
    for code in countries:
        kinds = {
            str(getattr(e, "kind", "") or "")
            for e in ranked
            if str(getattr(e, "node", "") or "").upper() == code
        }
        if "labor_strike" in kinds or "demand_contraction" in kinds:
            cast.append(AgentPersona(
                agent_id=f"labor-{code}",
                role="Công đoàn" if vi else "Labor",
                country=code,
                objective=(
                    "Bảo vệ lương thực và việc làm; phản ứng trước bất mãn hộ gia đình."
                    if vi else
                    "Defend real wages and jobs; react to household grievance."
                ),
                can_set_policy=False,
                allowed_actions=actions_for_role(can_set_policy=False),
            ))
            cast.append(AgentPersona(
                agent_id=f"corp-{code}",
                role="Doanh nghiệp" if vi else "Corporate",
                country=code,
                objective=(
                    "Bảo vệ biên lợi nhuận và chuỗi cung ứng; phản bác yêu cầu không khả thi."
                    if vi else
                    "Protect margins and supply chains; push back on unviable demands."
                ),
                can_set_policy=False,
                allowed_actions=actions_for_role(can_set_policy=False),
            ))
        cast.append(AgentPersona(
            agent_id=f"hh-{code}",
            role="Hộ gia đình" if vi else "Household",
            country=code,
            objective=(
                "Cân bằng tiêu dùng và tiết kiệm phòng ngừa trước lạm phát / thất nghiệp."
                if vi else
                "Balance consumption vs precautionary saving under inflation / jobs stress."
            ),
            can_set_policy=False,
            allowed_actions=actions_for_role(can_set_policy=False),
        ))
        cast.append(AgentPersona(
            agent_id=f"cb-{code}",
            role="Ngân hàng trung ương" if vi else "Central Bank",
            country=code,
            objective=(
                "Ổn định giá và việc làm; chỉ hành động khi số liệu grounding yêu cầu."
                if vi else
                "Stabilize prices and employment; act only when grounded metrics require it."
            ),
            can_set_policy=True,
            allowed_actions=actions_for_role(can_set_policy=True),
        ))
        if "safe_haven_migration" in kinds or "demand_contraction" in kinds:
            cast.append(AgentPersona(
                agent_id=f"gov-{code}",
                role="Chính phủ" if vi else "Government",
                country=code,
                objective=(
                    "Cân bằng tăng trưởng, nợ công và ổn định xã hội."
                    if vi else
                    "Balance growth, public debt and social stability."
                ),
                can_set_policy=True,
                allowed_actions=actions_for_role(can_set_policy=True),
            ))
    # Speaking order prioritises conflict + an accountable policy responder.
    # Local 8B uses the first three: Labor -> Corporate -> Central Bank.
    order = ("labor-", "corp-", "cb-", "hh-", "gov-")
    cast.sort(key=lambda p: (
        countries.index(p.country) if p.country in countries else 99,
        next((i for i, pref in enumerate(order) if p.agent_id.startswith(pref)), 99),
    ))
    return cast[:8]


def parse_dialogue_json(
    raw: str,
    *,
    valid_codes: set[str],
    grounded: list[GroundedMetric],
    tick: int,
    locale: str = "en",
    cast: list[AgentPersona] | None = None,
) -> tuple[list[DialogueDecision], list[DialogueUtterance], int]:
    """Return (decisions, utterances, rejected_count)."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            logger.warning("dialogue JSON parse failed")
            return [], [], 1
        try:
            payload = json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            logger.warning("dialogue JSON parse failed")
            return [], [], 1

    if not isinstance(payload, dict):
        return [], [], 1

    allowed_event_ids = {m.event_id for m in grounded if m.event_id}
    cast_by_id = {p.agent_id: p for p in (cast or [])}
    rejected = 0

    # Prefer explicit multi-turn ``turns``; fall back to decisions+utterances.
    turns = payload.get("turns")
    if isinstance(turns, list) and turns:
        decisions: list[DialogueDecision] = []
        utterances: list[DialogueUtterance] = []
        seen_ids: list[str] = []
        for idx, row in enumerate(turns):
            if not isinstance(row, dict):
                rejected += 1
                continue
            agent_id = str(row.get("agent_id", "")).strip()
            code = str(row.get("country", "")).upper()
            role = str(row.get("role", "Agent"))
            action = str(row.get("action_id", "hold")).strip().lower()
            responds_to = str(row.get("responds_to", "") or "").strip()
            persona = cast_by_id.get(agent_id)
            if persona is not None:
                # Identity comes from the server-built cast, never from model
                # prose (prevents a Labor id from spoofing Central Bank powers).
                code = persona.country
                role = persona.role

            if code and code not in valid_codes:
                rejected += 1
                continue
            if action not in ACTION_ENUMS:
                rejected += 1
                continue
            if persona is not None and action not in persona.allowed_actions:
                rejected += 1
                continue
            if persona is None and role not in POLICY_ROLES and action not in {
                "hold", "bargain_accept", "bargain_resist",
            }:
                rejected += 1
                continue
            # Turns after the first must answer someone already on the floor.
            if idx > 0 and responds_to and responds_to not in seen_ids:
                # Soft: clear invalid pointer rather than drop the whole turn.
                responds_to = seen_ids[-1] if seen_ids else ""

            grounding = [str(g) for g in (row.get("grounding") or [])][:8]
            if not grounding or not any(g in allowed_event_ids for g in grounding):
                # Auto-attach the first available event for this country if present.
                auto = next(
                    (eid for eid in allowed_event_ids if eid.startswith(f"{code}:")),
                    "",
                )
                if auto:
                    grounding = [auto]
                else:
                    rejected += 1
                    continue

            can_policy = (
                persona.can_set_policy if persona is not None else role in POLICY_ROLES
            )
            base = dict(ACTION_TO_DELTAS.get(action, {}))
            if not can_policy:
                # Strip macro levers from non-policy actors.
                base = {k: v for k, v in base.items() if k == "stance"}

            try:
                conf = _clip(float(row.get("confidence", 0.55)), 0.0, 1.0)
            except (TypeError, ValueError):
                conf = 0.55

            text = strip_ungrounded_numbers(str(row.get("text", "")), grounded)
            if not text:
                rejected += 1
                continue

            decisions.append(
                DialogueDecision(
                    agent_id=agent_id or f"{code}-{action}",
                    country=code,
                    action_id=action,
                    params=base,
                    rationale=str(row.get("rationale", ""))[:280],
                    grounding=grounding,
                    confidence=conf,
                    responds_to=responds_to,
                )
            )
            utterances.append(
                DialogueUtterance(
                    agent_id=agent_id or code or "agent",
                    role=role,
                    country=code,
                    text=text[:420],
                    locale=locale,
                    metrics=list(grounded[:6]),
                    responds_to=responds_to,
                )
            )
            seen_ids.append(agent_id or f"{code}-{idx}")
        return decisions, utterances, rejected

    # Legacy shape -----------------------------------------------------------
    decisions = []
    for row in payload.get("decisions", []) or []:
        if not isinstance(row, dict):
            rejected += 1
            continue
        code = str(row.get("country", "")).upper()
        action = str(row.get("action_id", "hold")).strip().lower()
        if code not in valid_codes or action not in ACTION_ENUMS:
            rejected += 1
            continue
        grounding = [str(g) for g in (row.get("grounding") or [])][:8]
        if not grounding or not any(g in allowed_event_ids for g in grounding):
            rejected += 1
            continue
        base = dict(ACTION_TO_DELTAS.get(action, {}))
        try:
            conf = _clip(float(row.get("confidence", 0.5)), 0.0, 1.0)
        except (TypeError, ValueError):
            conf = 0.5
        decisions.append(
            DialogueDecision(
                agent_id=str(row.get("agent_id", f"{code}-{action}")),
                country=code,
                action_id=action,
                params=base,
                rationale=str(row.get("rationale", ""))[:280],
                grounding=grounding,
                confidence=conf,
                responds_to=str(row.get("responds_to", "") or ""),
            )
        )

    utterances = []
    for row in payload.get("utterances", []) or []:
        if not isinstance(row, dict):
            rejected += 1
            continue
        code = str(row.get("country", "")).upper()
        if code and code not in valid_codes:
            rejected += 1
            continue
        text = strip_ungrounded_numbers(str(row.get("text", "")), grounded)
        if not text:
            rejected += 1
            continue
        utterances.append(
            DialogueUtterance(
                agent_id=str(row.get("agent_id", code or "agent")),
                role=str(row.get("role", "Agent")),
                country=code,
                text=text[:420],
                locale=locale,
                metrics=list(grounded[:6]),
                responds_to=str(row.get("responds_to", "") or ""),
            )
        )
    return decisions, utterances, rejected


def decisions_to_directives(
    decisions: list[DialogueDecision],
    *,
    cast: list[AgentPersona] | None = None,
) -> dict[str, GovernorDirective]:
    """Map validated *policy* decisions onto clamped GovernorDirective overlays.

    Labor / Corporate bargain moves do not become interest-rate / tax levers.
    When multiple policy actors speak for one country, the last one wins (CB/Gov
    speak after Labor/Corporate in the cast order).
    """
    cast_by_id = {p.agent_id: p for p in (cast or [])}
    out: dict[str, GovernorDirective] = {}
    for d in decisions:
        persona = cast_by_id.get(d.agent_id)
        can_policy = True if persona is None else persona.can_set_policy
        if not can_policy:
            continue
        if d.action_id in ("bargain_accept", "bargain_resist"):
            continue
        p = d.params
        if not any(
            abs(float(p.get(k, 0.0))) > 1e-12
            for k in (
                "interest_rate_delta",
                "tariff_delta",
                "money_supply_delta",
                "tax_delta",
            )
        ):
            continue
        out[d.country] = GovernorDirective(
            code=d.country,
            interest_rate_delta=float(p.get("interest_rate_delta", 0.0)),
            tariff_delta=float(p.get("tariff_delta", 0.0)),
            money_supply_delta=float(p.get("money_supply_delta", 0.0)),
            tax_delta=float(p.get("tax_delta", 0.0)),
            stance=float(p.get("stance", 0.0)),
            rationale=d.rationale or d.action_id,
        ).clamped()
    return out


def build_fallback_bundle(
    *,
    tick: int,
    events: list[Any],
    locale: str = "en",
    material_reason: str = "",
) -> DialogueTurnBundle:
    """Status-only bundle when LLM is unavailable.

    Intentionally emits **no policy decisions**. Hardcoded tighten/ease used to
    drive physics here and made every nation look scripted. Control-law
    governors still run via HierarchyBroker independently.
    """
    cast = build_cast(events, locale=locale)
    grounded: list[GroundedMetric] = []
    utterances: list[DialogueUtterance] = []
    vi = locale.startswith("vi")
    for e in events[:4]:
        node = getattr(e, "node", "") or ""
        kind = getattr(e, "kind", "") or ""
        intensity = float(getattr(e, "intensity", 0.0) or 0.0)
        metrics = dict(getattr(e, "metrics", {}) or {})
        gm = [GroundedMetric("intensity", intensity, "", tick, f"{node}:{kind}")]
        for k, v in list(metrics.items())[:4]:
            try:
                gm.append(GroundedMetric(str(k), float(v), "", tick, f"{node}:{kind}"))
            except (TypeError, ValueError):
                continue
        grounded.extend(gm)

        if kind == "labor_strike":
            d = float(metrics.get("dissatisfaction", intensity) or intensity)
            text = (
                f"{node}: bất mãn ~{d*100:.0f}% — đang chờ các bên thương lượng."
                if vi else
                f"{node}: dissatisfaction ~{d*100:.0f}% — awaiting bargaining among agents."
            )
            role = "Công đoàn" if vi else "Labor"
            agent_id = f"labor-{node}"
        elif kind == "demand_contraction":
            ci = float(metrics.get("consumption_index", max(0.0, 1.0 - intensity)) or 0.0)
            text = (
                f"{node}: cầu tiêu dùng ~{ci*100:.0f}% xu hướng — hộ gia đình đang quan sát."
                if vi else
                f"{node}: demand ~{ci*100:.0f}% of trend — households observing."
            )
            role = "Hộ gia đình" if vi else "Household"
            agent_id = f"hh-{node}"
        elif kind == "safe_haven_migration":
            text = (
                f"{node}: dòng vốn trú ẩn tăng (cường độ {intensity:.2f}) — chưa có quyết định chính sách."
                if vi else
                f"{node}: safe-haven outflow intensifies ({intensity:.2f}) — no policy call yet."
            )
            role = "Hộ gia đình" if vi else "Household"
            agent_id = f"hh-{node}"
        else:
            text = (
                f"{node}: tín hiệu {kind} (cường độ {intensity:.2f})."
                if vi else
                f"{node}: {kind} signal (intensity {intensity:.2f})."
            )
            role = "Hộ gia đình" if vi else "Household"
            agent_id = f"hh-{node}"

        utterances.append(
            DialogueUtterance(
                agent_id=agent_id,
                role=role,
                country=node,
                text=strip_ungrounded_numbers(text, gm),
                locale=locale,
                metrics=gm,
            )
        )

    return DialogueTurnBundle(
        tick=tick,
        decisions=[],  # no hardcode policy
        utterances=utterances,
        source="status",
        rejected=0,
        material_reason=material_reason,
        level="info",
        cast=[p.as_dict() for p in cast],
    )
