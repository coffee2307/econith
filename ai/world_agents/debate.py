"""ECONITH :: ai.world_agents.debate

Multi-agent macro debate for the World pillar. Hub-nation personas exchange
views on the live macro matrix; output is localized (en/vi) for the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from core.llm_pool import LLMKeyPool, parse_llm_api_keys

logger = logging.getLogger("econith.ai.world_agents")

AGENTS = (
    {"id": "usa_fed", "country": "USA", "role_en": "Fed Chair", "role_vi": "Chủ tịch Fed"},
    {"id": "chn_pboc", "country": "CHN", "role_en": "PBoC Governor", "role_vi": "Thống đốc NHNN TQ"},
    {"id": "vnm_mof", "country": "VNM", "role_en": "Finance Minister", "role_vi": "Bộ trưởng Tài chính"},
    {"id": "deu_ecb", "country": "DEU", "role_en": "ECB Council", "role_vi": "Hội đồng ECB"},
)

__all__ = ["synthesize_agent_exchange"]


def _role(agent: dict[str, str], locale: str) -> str:
    return agent["role_vi"] if locale == "vi" else agent["role_en"]


def _macro_line(code: str, snap: dict[str, Any], locale: str) -> str:
    growth = float(snap.get("gdp_growth", 0) or 0) * 100.0
    inflation = float(snap.get("inflation", 0) or 0) * 100.0
    unrest = float((snap.get("vectors") or {}).get("geopolitical", {}).get("social_unrest_index", 0) or 0)
    if locale == "vi":
        return (
            f"Tăng trưởng {growth:+.1f}%, lạm phát {inflation:.1f}%, "
            f"bất ổn xã hội {unrest * 100:.0f}%."
        )
    return (
        f"Growth {growth:+.1f}%, inflation {inflation:.1f}%, "
        f"social unrest {unrest * 100:.0f}%."
    )


def _template_exchange(
    countries: dict[str, Any],
    *,
    locale: str,
    topic: str | None,
) -> list[dict[str, str]]:
    topic_line = topic or (
        "Điều chỉnh chính sách vĩ mô khu vực"
        if locale == "vi"
        else "Regional macro policy coordination"
    )
    lines: list[dict[str, str]] = []
    for agent in AGENTS:
        snap = countries.get(agent["country"], {}) or {}
        role = _role(agent, locale)
        macro = _macro_line(agent["country"], snap, locale)
        if locale == "vi":
            text = (
                f"Về chủ đề «{topic_line}»: {macro} "
                f"Chúng tôi ưu tiên ổn định tài chính và giảm rủi ro lan truyền."
            )
        else:
            text = (
                f"On «{topic_line}»: {macro} "
                f"We prioritise financial stability and limiting cross-border spillovers."
            )
        lines.append(
            {
                "agent_id": agent["id"],
                "country": agent["country"],
                "role": role,
                "text": text,
            }
        )
    return lines


def _build_prompt(
    countries: dict[str, Any],
    *,
    locale: str,
    topic: str | None,
) -> str:
    lang = "Vietnamese" if locale == "vi" else "English"
    topic_line = topic or "current global macro tensions"
    facts = []
    for agent in AGENTS:
        snap = countries.get(agent["country"], {}) or {}
        facts.append(f"{agent['country']} ({_role(agent, locale)}): {_macro_line(agent['country'], snap, locale)}")
    facts_text = "\n".join(f"- {f}" for f in facts)
    return (
        f"Write a short multi-agent policy debate in {lang} about: {topic_line}.\n"
        f"Each speaker is one hub-nation official. One sentence per agent, "
        f"disagreement allowed, no hype.\n"
        f"Macro facts:\n{facts_text}\n"
        f"Return exactly 4 lines prefixed with USA:, CHN:, VNM:, DEU:"
    )


def _parse_llm_lines(raw: str, locale: str) -> list[dict[str, str]]:
    by_country: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for agent in AGENTS:
            prefix = f"{agent['country']}:"
            if stripped.upper().startswith(prefix):
                by_country[agent["country"]] = stripped[len(prefix) :].strip()
                break
    lines: list[dict[str, str]] = []
    for agent in AGENTS:
        text = by_country.get(agent["country"], "").strip()
        if not text:
            continue
        lines.append(
            {
                "agent_id": agent["id"],
                "country": agent["country"],
                "role": _role(agent, locale),
                "text": text,
            }
        )
    return lines


async def synthesize_agent_exchange(
    countries: dict[str, Any],
    *,
    locale: str = "en",
    topic: str | None = None,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Return localized agent debate lines for the World UI."""
    locale = "vi" if locale.lower().startswith("vi") else "en"
    keys = parse_llm_api_keys(api_key)
    if not keys or not base_url or not model:
        return {
            "lines": _template_exchange(countries, locale=locale, topic=topic),
            "source": "template",
            "locale": locale,
        }

    prompt = _build_prompt(countries, locale=locale, topic=topic)
    pool = LLMKeyPool(keys)

    def _call() -> str:
        response = pool.create_chat_completion(
            base_url=base_url,
            model=model,
            timeout=25.0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You simulate a macro policy debate between nation agents. "
                        "Be concise and factual."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.35,
            max_tokens=320,
        )
        return (response.choices[0].message.content or "").strip()

    try:
        raw = await asyncio.to_thread(_call)
        lines = _parse_llm_lines(raw, locale)
        if len(lines) < 2:
            raise ValueError("insufficient parsed debate lines")
        return {"lines": lines, "source": "llm", "locale": locale}
    except Exception:
        logger.exception("world agent exchange LLM failed; using template")
        return {
            "lines": _template_exchange(countries, locale=locale, topic=topic),
            "source": "template",
            "locale": locale,
        }
