"""ECONITH :: ai.world_agents.debate

On-demand multi-agent macro deliberation for the World pillar.

Agents are cast dynamically from live country stress (not a fixed USA/CHN/VNM/DEU
script). Each speaker must answer the previous one. Numbers must come from the
provided macro snapshot — invented magnitudes are stripped.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from core.llm_pool import LLMKeyPool, RoutedLLMPool, parse_llm_api_keys
from econith.world.agents.state_arrays import EmergentEvent
from econith.world.core.dialogue_schema import ACTION_ENUMS
from econith.world.core.dialogue_validator import (
    build_cast,
    parse_dialogue_json,
    strip_ungrounded_numbers,
)
from econith.world.core.dialogue_schema import GroundedMetric

logger = logging.getLogger("econith.ai.world_agents")

__all__ = ["synthesize_agent_exchange"]


def _stress_events(countries: dict[str, Any]) -> list[EmergentEvent]:
    """Derive material-ish events from the live country matrix (no scripts)."""
    events: list[EmergentEvent] = []
    for code, snap in (countries or {}).items():
        if not isinstance(snap, dict):
            continue
        code_u = str(code).upper()
        growth = float(snap.get("gdp_growth", 0.0) or 0.0)
        inflation = float(snap.get("inflation", snap.get("inflation_cpi", 0.0)) or 0.0)
        vectors = snap.get("vectors") or {}
        unrest = float((vectors.get("geopolitical") or {}).get("social_unrest_index", 0.0) or 0.0)
        unemployment = float(snap.get("unemployment", 0.05) or 0.05)
        # Dissatisfaction proxy from live stress — not a canned story.
        dissatisfaction = max(
            0.0,
            min(
                1.0,
                0.35 * unrest
                + 0.30 * max(0.0, inflation - 0.03) / 0.07
                + 0.25 * max(0.0, unemployment - 0.05) / 0.10
                + 0.20 * max(0.0, -growth) / 0.05,
            ),
        )
        consumption_index = max(
            0.05,
            min(1.4, 1.0 + growth * 4.0 - max(0.0, inflation - 0.025) * 3.0 - unrest * 0.25),
        )
        if dissatisfaction >= 0.92:
            events.append(EmergentEvent(
                node=code_u,
                kind="labor_strike",
                intensity=dissatisfaction,
                metrics={
                    "dissatisfaction": dissatisfaction,
                    "consumption_index": consumption_index,
                    "unemployment": unemployment,
                    "inflation_cpi": inflation,
                },
            ))
        elif consumption_index < 0.70:
            events.append(EmergentEvent(
                node=code_u,
                kind="demand_contraction",
                intensity=min(1.0, (0.70 - consumption_index) / 0.3),
                metrics={
                    "consumption_index": consumption_index,
                    "inflation_cpi": inflation,
                    "gdp_growth": growth,
                },
            ))
        elif growth > 0.04 and inflation < 0.03:
            events.append(EmergentEvent(
                node=code_u,
                kind="demand_expansion",
                intensity=min(1.0, growth / 0.06),
                metrics={
                    "consumption_index": consumption_index,
                    "gdp_growth": growth,
                    "inflation_cpi": inflation,
                },
            ))
    events.sort(key=lambda e: e.intensity, reverse=True)
    return events[:4]


def _grounded_metrics(events: list[EmergentEvent], countries: dict[str, Any]) -> list[GroundedMetric]:
    out: list[GroundedMetric] = []
    for e in events:
        eid = f"{e.node}:{e.kind}"
        out.append(GroundedMetric("intensity", float(e.intensity), "", 0, eid))
        for k, v in (e.metrics or {}).items():
            out.append(GroundedMetric(str(k), float(v), "", 0, eid))
        snap = countries.get(e.node) or countries.get(e.node.title()) or {}
        if isinstance(snap, dict):
            for name, key in (
                ("interest_rate", "interest_rate"),
                ("inflation_cpi", "inflation"),
                ("gdp_growth", "gdp_growth"),
                ("unemployment", "unemployment"),
            ):
                if key in snap or (key == "inflation" and "inflation" in snap):
                    try:
                        val = float(snap.get(key, snap.get("inflation", 0.0)) or 0.0)
                        out.append(GroundedMetric(name, val, "", 0, eid))
                    except (TypeError, ValueError):
                        continue
    return out


def _status_lines(
    events: list[EmergentEvent],
    *,
    locale: str,
    grounded: list[GroundedMetric],
) -> list[dict[str, str]]:
    """Honest status readout when LLM is unavailable — facts only, no fake opinions."""
    vi = locale.startswith("vi")
    lines: list[dict[str, str]] = []
    for e in events[:4]:
        gm = [m for m in grounded if m.event_id == f"{e.node}:{e.kind}"]
        if e.kind == "labor_strike":
            d = float((e.metrics or {}).get("dissatisfaction", e.intensity))
            text = (
                f"{e.node}: bất mãn ~{d*100:.0f}% — chưa có tranh luận LLM (đang chờ API)."
                if vi else
                f"{e.node}: dissatisfaction ~{d*100:.0f}% — LLM debate pending (API unavailable)."
            )
            role = "Công đoàn" if vi else "Labor"
        elif e.kind == "demand_contraction":
            ci = float((e.metrics or {}).get("consumption_index", 0.0))
            text = (
                f"{e.node}: cầu ~{ci*100:.0f}% xu hướng — chưa có tranh luận LLM."
                if vi else
                f"{e.node}: demand ~{ci*100:.0f}% of trend — LLM debate pending."
            )
            role = "Hộ gia đình" if vi else "Household"
        else:
            text = (
                f"{e.node}: {e.kind} (cường độ {e.intensity:.2f})."
                if vi else
                f"{e.node}: {e.kind} (intensity {e.intensity:.2f})."
            )
            role = "Hộ gia đình" if vi else "Household"
        lines.append({
            "agent_id": f"status-{e.node}",
            "country": e.node,
            "role": role,
            "text": strip_ungrounded_numbers(text, gm or grounded),
            "responds_to": "",
        })
    return lines


def _macro_brief(countries: dict[str, Any], codes: set[str]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for code in codes:
        snap = countries.get(code) or {}
        if not isinstance(snap, dict):
            continue
        out[code] = {
            "interest_rate": float(snap.get("interest_rate", 0.03) or 0.03),
            "inflation_cpi": float(snap.get("inflation", 0.025) or 0.025),
            "gdp_growth": float(snap.get("gdp_growth", 0.02) or 0.02),
            "unemployment": float(snap.get("unemployment", 0.05) or 0.05),
        }
    return out


async def synthesize_agent_exchange(
    countries: dict[str, Any],
    *,
    locale: str = "en",
    topic: str | None = None,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    local_enabled: bool = True,
    local_base_url: str = "http://localhost:11434/v1",
    local_model: str = "llama3:8b",
    local_first: bool = True,
) -> dict[str, Any]:
    """Return a multi-agent debate grounded in the live country matrix."""
    locale = "vi" if locale.lower().startswith("vi") else "en"
    events = _stress_events(countries)
    if not events:
        # Mild ambient — still cast two hubs if present so the panel isn't empty.
        for code in ("USA", "CHN", "VNM", "DEU", "JPN", "IND"):
            if code in countries:
                snap = countries[code] or {}
                events.append(EmergentEvent(
                    node=code,
                    kind="demand_expansion" if float(snap.get("gdp_growth", 0) or 0) > 0 else "demand_contraction",
                    intensity=0.40,
                    metrics={
                        "gdp_growth": float(snap.get("gdp_growth", 0) or 0),
                        "inflation_cpi": float(snap.get("inflation", 0) or 0),
                        "consumption_index": 1.0,
                    },
                ))
            if len(events) >= 2:
                break

    grounded = _grounded_metrics(events, countries)
    cast = build_cast(events, locale=locale)
    keys = parse_llm_api_keys(api_key)

    if (not keys and not local_enabled) or not cast:
        return {
            "lines": _status_lines(events, locale=locale, grounded=grounded),
            "source": "status",
            "locale": locale,
            "cast": [p.as_dict() for p in cast],
        }

    lang = "Vietnamese" if locale == "vi" else "English"
    topic_line = topic or (
        "Ứng phó với các tín hiệu vĩ mô vừa xuất hiện"
        if locale == "vi"
        else "Responding to the latest material macro signals"
    )
    brief = {
        "language": lang,
        "topic": topic_line,
        "protocol": [
            "Speak in cast order.",
            "After the first speaker, every turn MUST set responds_to to a prior agent_id and answer their point.",
            "At least one disagreement is required.",
            "Only Central Bank / Government may ease/tighten/qe_pulse/tax_*/tariff_*.",
            "Labor/Corporate/Household: hold / bargain_accept / bargain_resist only.",
            "Use ONLY numbers from grounded_metrics.",
            "Stay proportional to event intensity — no apocalyptic or fantasy policy.",
        ],
        "action_allowlist": sorted(ACTION_ENUMS),
        "cast": [p.as_dict() for p in cast],
        "grounded_metrics": [m.as_dict() for m in grounded[:24]],
        "events": [
            {
                "node": e.node,
                "kind": e.kind,
                "intensity": round(float(e.intensity), 3),
                "metrics": {k: round(float(v), 4) for k, v in (e.metrics or {}).items()},
            }
            for e in events
        ],
        "macro": _macro_brief(countries, {p.country for p in cast}),
    }
    prompt = (
        "Run a multi-agent policy deliberation. Return JSON "
        '{"turns":[{"agent_id":str,"role":str,"country":str,"text":str,'
        '"responds_to":str|null,"action_id":str,"grounding":[str]}]}. '
        f"Produce exactly {min(3, len(cast))} turns using the first cast members "
        "in order. Each text must be one "
        "sentence and <= 35 words; rationale <= 8 words.\n\n"
        + json.dumps(brief, ensure_ascii=False)
    )

    remote_pool = LLMKeyPool(keys) if keys else None
    pool = RoutedLLMPool(
        remote_pool,
        local_enabled=local_enabled,
        local_base_url=local_base_url,
        local_model=local_model,
        local_first=local_first,
    )

    def _call() -> str:
        response = pool.create_chat_completion(
            base_url=base_url,
            model=model,
            timeout=35.0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You simulate sovereign / corporate / labour / household agents "
                        "debating live macro conditions. Each agent has a private objective "
                        "and must respond to the previous speaker. Strict JSON only. "
                        "Never invent magnitudes — only grounded_metrics numbers."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.55,
            max_tokens=1100,
        )
        return (response.choices[0].message.content or "").strip()

    try:
        raw = await asyncio.to_thread(_call)
        decisions, utterances, rejected = parse_dialogue_json(
            raw,
            valid_codes={p.country for p in cast},
            grounded=grounded,
            tick=0,
            locale=locale,
            cast=cast,
        )
        if len(utterances) < 2:
            raise ValueError(f"insufficient turns after validation (rejected={rejected})")
        lines = [
            {
                "agent_id": u.agent_id,
                "country": u.country,
                "role": u.role,
                "text": u.text,
                "responds_to": u.responds_to,
                "action_id": next(
                    (d.action_id for d in decisions if d.agent_id == u.agent_id),
                    "",
                ),
            }
            for u in utterances
        ]
        return {
            "lines": lines,
            "source": "llm",
            "locale": locale,
            "cast": [p.as_dict() for p in cast],
            "rejected": rejected,
        }
    except Exception:
        logger.exception("world agent exchange LLM failed; returning grounded status")
        return {
            "lines": _status_lines(events, locale=locale, grounded=grounded),
            "source": "status",
            "locale": locale,
            "cast": [p.as_dict() for p in cast],
        }
