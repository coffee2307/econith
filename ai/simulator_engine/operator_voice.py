"""ECONITH :: operator-facing LLM voice (locale without string templates).

Human-facing World lines (Events / Agents) are rewritten by the local LLM into
the dashboard language. Machine prompts for the scenario parser stay English.

When the LLM refuses to write Vietnamese, callers must supply a deterministic
fallback — this module never silently returns English for locale=vi.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger("econith.world.operator_voice")

_VI_CHARS = (
    "ăâêôơưđĂÂÊÔƠƯĐ"
    "áàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ"
)

_EN_MARKERS = re.compile(
    r"\b("
    r"the|and|with|from|that|this|what|when|where|which|while|"
    r"if\s+i|applied|implement|increasing|decreasing|interest\s+rate|"
    r"what\s+happens|corporate\s+desk|fiscal\s+policy|monetary\s+policy|"
    r"trade\s+balance|economic\s+growth|aiming\s+to|particularly|"
    r"assessment|however|proposed|experiment|because|allows|"
    r"government\s+ai|corporate\s+ai|societal\s+ai"
    r")\b",
    re.IGNORECASE,
)


def language_name(locale: str) -> str:
    return "Vietnamese" if (locale or "en").lower().startswith("vi") else "English"


def _vi_char_count(text: str) -> int:
    return sum(1 for ch in text if ch in _VI_CHARS)


def _latin_letter_count(text: str) -> int:
    return sum(1 for ch in text if ("a" <= ch.lower() <= "z"))


def _looks_vietnamese(text: str) -> bool:
    """Legacy soft check (any diacritic). Prefer :func:`is_operator_locale`."""
    return _vi_char_count(text or "") > 0


def is_operator_locale(text: str, locale: str) -> bool:
    """True when ``text`` is acceptable operator prose for ``locale``.

    For Vietnamese: require enough diacritics relative to Latin letters, and
    reject sentences dominated by English function words / policy phrases.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if not (locale or "").lower().startswith("vi"):
        return not _looks_vietnamese(cleaned)

    latin = _latin_letter_count(cleaned)
    vi = _vi_char_count(cleaned)
    if latin == 0:
        # Numbers / ISO codes only — treat as locale-neutral OK.
        return True
    if vi / max(latin, 1) < 0.08:
        return False
    if vi < 2 and latin > 20:
        return False
    en_hits = len(_EN_MARKERS.findall(cleaned))
    if en_hits >= 2:
        return False
    if en_hits >= 1 and vi / max(latin, 1) < 0.15:
        return False
    return True


def chat_text(
    llm_pool: Any,
    *,
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 180,
    temperature: float = 0.4,
) -> str:
    if llm_pool is None:
        return ""
    try:
        resp = llm_pool.create_chat_completion(
            base_url=base_url or "http://localhost:11434/v1",
            model=model or "llama3:8b",
            timeout=45.0,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        try:
            return (resp.choices[0].message.content or "").strip()
        except (AttributeError, IndexError, TypeError):
            return str(resp).strip()
    except Exception:  # noqa: BLE001
        logger.warning("operator_voice LLM call failed", exc_info=True)
        return ""


def rewrite_line(
    llm_pool: Any,
    *,
    base_url: str,
    model: str,
    locale: str,
    source_text: str,
    context: str = "",
) -> str:
    """Rewrite one operator line into the dashboard language via LLM.

    When target is Vietnamese and translation fails, returns ``""`` so the
    caller can substitute a deterministic template (never silent English).
    """
    text = (source_text or "").strip()
    if not text or llm_pool is None:
        return text if not (locale or "").lower().startswith("vi") else ("" if text else "")
    lang = language_name(locale)
    vi_target = lang == "Vietnamese"
    if vi_target and is_operator_locale(text, "vi"):
        return text
    if not vi_target and is_operator_locale(text, "en"):
        return text
    messages = [
        {
            "role": "system",
            "content": (
                f"You are a professional translator for an economic simulator UI. "
                f"Translate the operator line into natural {lang}. "
                "Return ONLY the translated sentence. "
                "Keep ISO country codes (USA, CHN, DEU) and metric keys "
                "(interest_rate, gdp_growth) and numbers unchanged. "
                "Do NOT keep English prose. Do not add 'Translation:' or actor labels."
            ),
        },
        {
            "role": "user",
            "content": (
                (f"Context: {context}\n" if context else "")
                + f"Target language: {lang}\n"
                f"Line to translate:\n{text}"
            ),
        },
    ]
    out = chat_text(
        llm_pool,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=200,
        temperature=0.1,
    )
    out = (out or "").strip()
    if vi_target:
        if out and is_operator_locale(out, "vi"):
            return out
        retry = chat_text(
            llm_pool,
            base_url=base_url,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Dịch sang tiếng Việt tự nhiên. Chỉ trả về câu tiếng Việt. "
                        "Giữ mã ISO và số. Cấm để nguyên câu tiếng Anh."
                    ),
                },
                {"role": "user", "content": out or text},
            ],
            max_tokens=200,
            temperature=0.1,
        )
        if retry and is_operator_locale(retry.strip(), "vi"):
            return retry.strip()
        return ""
    return out or text


def ensure_locale_text(
    llm_pool: Any,
    *,
    base_url: str,
    model: str,
    locale: str,
    text: str,
    context: str = "",
    fallback: str | None = None,
) -> str:
    """Strip actor prefixes then force the dashboard language.

    For locale=vi: never returns English source. Uses ``fallback`` (or ``""``)
    when LLM rewrite fails the Vietnamese quality gate.
    """
    cleaned = (text or "").strip()
    for prefix in (
        "Government AI:",
        "Corporate AI:",
        "Societal AI:",
        "Chair:",
        "AI chính phủ:",
        "AI doanh nghiệp:",
        "AI xã hội:",
    ):
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix) :].strip()
    if not cleaned:
        return cleaned
    if not (locale or "").lower().startswith("vi"):
        return cleaned
    if is_operator_locale(cleaned, "vi"):
        return cleaned
    rewritten = rewrite_line(
        llm_pool,
        base_url=base_url,
        model=model,
        locale=locale,
        source_text=cleaned,
        context=context,
    )
    if rewritten and is_operator_locale(rewritten, "vi"):
        return rewritten
    if fallback is not None and is_operator_locale(fallback, "vi"):
        return fallback.strip()
    if fallback is not None and fallback.strip():
        return fallback.strip()
    return ""


def compose_event_from_facts(
    llm_pool: Any,
    *,
    base_url: str,
    model: str,
    locale: str,
    facts: dict[str, Any],
) -> str:
    """Compose one Events-tab line from structured facts (no string templates)."""
    if llm_pool is None:
        return ""
    lang = language_name(locale)
    vi = lang == "Vietnamese"
    messages = [
        {
            "role": "system",
            "content": (
                f"You are the Events ticker for an economic simulator. "
                f"Write ONE short operator line in {lang} only. "
                + (
                    "Do not use English words except ISO codes (USA, CHN) and metric keys. "
                    if vi
                    else ""
                )
                + "No bullet lists. Keep codes/numbers exact. "
                "Do not start with 'OPERATOR EVENT' or 'Translation:'."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language required: {lang}\n"
                "Facts:\n"
                + "\n".join(f"- {k}: {v}" for k, v in facts.items() if v is not None)
            ),
        },
    ]
    out = chat_text(
        llm_pool,
        base_url=base_url,
        model=model,
        messages=messages,
        max_tokens=140,
        temperature=0.35,
    )
    if vi and out and not is_operator_locale(out, "vi"):
        out = rewrite_line(
            llm_pool,
            base_url=base_url,
            model=model,
            locale=locale,
            source_text=out,
            context="events ticker",
        )
    if vi and out and not is_operator_locale(out, "vi"):
        return ""
    return out or ""
