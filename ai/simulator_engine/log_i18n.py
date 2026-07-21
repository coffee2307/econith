"""ECONITH :: ai.simulator_engine.log_i18n

Locale-aware Event Log lines for hypothesis / scenario (matches NarrativeEngine).
Internal parse prompts stay English; only operator-facing strings are localized.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ai.simulator_engine.narrative import NarrativeEngine

if TYPE_CHECKING:
    from ai.simulator_engine.hypothesis_schema import Hypothesis, HypothesisOutcome

_engine = NarrativeEngine()

_CATEGORY_EN: dict[str, str] = {
    "tariff": "tariff shock",
    "monetary_tighten": "monetary tightening",
    "monetary_ease": "monetary easing",
    "sanctions": "sanctions",
    "conflict": "conflict shock",
    "pandemic": "pandemic shock",
    "tech_breakthrough": "tech breakthrough",
    "sovereign_default": "sovereign default",
    "stimulus": "fiscal stimulus",
    "generic": "macro shock",
}

_CATEGORY_VI: dict[str, str] = {
    "tariff": "cú sốc thuế quan",
    "monetary_tighten": "thắt chặt tiền tệ",
    "monetary_ease": "nới lỏng tiền tệ",
    "sanctions": "trừng phạt",
    "conflict": "cú sốc xung đột",
    "pandemic": "cú sốc dịch bệnh",
    "tech_breakthrough": "đột phá công nghệ",
    "sovereign_default": "vỡ nợ quốc gia",
    "stimulus": "kích thích tài khóa",
    "generic": "cú sốc vĩ mô",
}


def _is_vi(locale: str) -> bool:
    return (locale or "en").lower().startswith("vi")


def _sev_word(severity: float, *, vi: bool) -> str:
    if severity > 0.85:
        return "cực mạnh" if vi else "catastrophic"
    if severity > 0.6:
        return "mạnh" if vi else "major"
    if severity > 0.4:
        return "vừa" if vi else "moderate"
    return "nhẹ" if vi else "mild"


def _places(subjects: list[str], *, vi: bool) -> str:
    if not subjects:
        return "toàn cầu" if vi else "Global"
    fmt = _engine._place_vi if vi else _engine._place_en  # noqa: SLF001
    return ", ".join(fmt(c) for c in subjects)


def hypothesis_summary(hyp: "Hypothesis", locale: str = "en") -> str:
    """Short operator-facing summary (structured; prompt kept for EN fallback)."""
    vi = _is_vi(locale)
    places = _places(list(hyp.subjects), vi=vi)
    sev = _sev_word(float(hyp.severity), vi=vi)
    cat = hyp.category or "generic"
    if vi:
        label = _CATEGORY_VI.get(cat, _CATEGORY_VI["generic"])
        if cat == "tariff" and len(hyp.subjects) >= 2:
            a = _engine._place_vi(hyp.subjects[0])  # noqa: SLF001
            b = _engine._place_vi(hyp.subjects[1])  # noqa: SLF001
            return f"{a} áp hàng rào thuế quan lên {b} (mức {sev})"
        return f"{label} tại {places} (mức {sev})"
    label = _CATEGORY_EN.get(cat, _CATEGORY_EN["generic"])
    if cat == "tariff" and len(hyp.subjects) >= 2:
        a = _engine._place_en(hyp.subjects[0])  # noqa: SLF001
        b = _engine._place_en(hyp.subjects[1])  # noqa: SLF001
        return f"{a} imposes a tariff barrier on {b} ({sev})"
    # EN: prefer the parse prompt when present (already English).
    if hyp.prompt.strip():
        return hyp.prompt.strip()
    return f"{sev} {label} affecting {places}"


def format_hypothesis_log(
    hyp: "Hypothesis",
    outcome: "HypothesisOutcome",
    locale: str = "en",
) -> str:
    vi = _is_vi(locale)
    if outcome.status == "ok":
        summary = hypothesis_summary(hyp, locale)
        n = len(outcome.deltas)
        if vi:
            return f"Giả thuyết {hyp.id}: {summary} ({n} biến động vĩ mô)"
        return f"Hypothesis {hyp.id}: {summary} ({n} macro deltas)"
    if outcome.status == "skipped":
        if vi:
            return f"Bỏ qua giả thuyết: {outcome.error or 'chưa kích hoạt'}"
        return f"Hypothesis skipped: {outcome.error or 'not armed'}"
    if vi:
        return f"Lỗi giả thuyết {hyp.id}: {outcome.error}"
    return f"Hypothesis {hyp.id} error: {outcome.error}"


def format_scenario_headline(
    category: str,
    severity: float,
    subject_names: list[str],
    locale: str = "en",
) -> str:
    vi = _is_vi(locale)
    names = ", ".join(subject_names) if subject_names else ("toàn cầu" if vi else "Global")
    sev = _sev_word(severity, vi=vi)
    if vi:
        label = _CATEGORY_VI.get(category, category.replace("_", " "))
        return f"Kịch bản {label} mức {sev} ảnh hưởng {names}"
    label = category.replace("_", " ")
    return f"{sev.title()} {label} scenario affecting {names}"


def format_scenario_log(
    prompt: str,
    narrative: str,
    effects: str,
    locale: str = "en",
) -> str:
    vi = _is_vi(locale)
    if vi:
        return f"Kịch bản «{prompt}» → {narrative}. Tác động: {effects}"
    return f"Scenario '{prompt}' -> {narrative}. Effects: {effects}"
