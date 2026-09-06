"""ECONITH :: deterministic Vietnamese operator lines for hypothesis Agents feed.

Used when the local LLM ignores the Vietnamese instruction. Keeps ISO codes
and numbers; never emits English prose when locale is vi.
"""
from __future__ import annotations

from typing import Any

from ai.simulator_engine.hypothesis_schema import Hypothesis


def _format_plan(hyp: Hypothesis) -> str:
    bits: list[str] = []
    for m in (hyp.mutations or [])[:4]:
        if not isinstance(m, dict):
            continue
        bits.append(f"{m.get('country')}.{m.get('field')}→{m.get('value')}")
    return ", ".join(bits) or (hyp.prompt or "kịch bản")


def vi_ask_line(hyp: Hypothesis) -> str:
    plan = _format_plan(hyp)
    return (
        f"Nếu điều chỉnh {plan}, tăng trưởng GDP, lạm phát và thất nghiệp "
        "các đối tác thay đổi thế nào?"
    )


def vi_act_line(hyp: Hypothesis) -> str:
    plan = list(hyp.mutations or [])
    if plan:
        m = plan[0]
        code = m.get("country")
        field = m.get("field")
        value = m.get("value")
        peers = ""
        if len(hyp.subjects) > 1:
            peers = " và các đối tác " + ",".join(hyp.subjects[1:3])
        return (
            f"Doanh nghiệp tiến hành điều chỉnh: đặt {code}.{field} = {value}. "
            f"Theo dõi lan tỏa sang tăng trưởng, thất nghiệp, lạm phát{peers}."
        )
    return f"Doanh nghiệp thực thi kịch bản «{hyp.prompt}» và đo phản ứng thị trường."


def vi_judge_line(
    hyp: Hypothesis,
    *,
    assessment: str = "",
    next_question: str = "",
) -> str:
    from ai.simulator_engine.operator_voice import is_operator_locale

    parts: list[str] = []
    if assessment and is_operator_locale(assessment, "vi"):
        parts.append(assessment.strip())
    if next_question and is_operator_locale(next_question, "vi"):
        parts.append(next_question.strip())
    if parts:
        return " ".join(parts)[:420]
    return (
        "Đánh giá: thí nghiệm đã chạy; hướng tiếp theo nên xoay biến số "
        "hoặc mở rộng lan tỏa sang thêm một quốc gia."
    )


def vi_debate_line(hyp: Hypothesis, actor: str) -> str:
    plan = _format_plan(hyp)
    sev = float(hyp.severity or 0.5)
    if actor == "Government AI":
        return f"Chính phủ đề xuất thí nghiệm: {plan}. Mục tiêu là đo phản ứng vĩ mô."
    if actor == "Corporate AI":
        return (
            "Doanh nghiệp lo ngại chi phí vốn và biên lợi nhuận biến động "
            f"khi cường độ ở mức {sev:.2f}."
        )
    if actor == "Societal AI":
        return "Xã hội cảnh báo áp lực lên việc làm và giá tiêu dùng nếu cú sốc quá mạnh."
    if actor == "Chair":
        return "Chủ tọa chấp thuận nhưng giữ bước điều chỉnh nhỏ để kiểm soát rủi ro."
    return f"Thảo luận thí nghiệm: {plan}."


def vi_fallback_for_phase(
    phase: str,
    hyp: Hypothesis,
    *,
    reflection: dict[str, Any] | None = None,
) -> str:
    """Deterministic Vietnamese line for a hypothesis Agents-tab phase."""
    phase = (phase or "").strip().lower()
    reflection = reflection or {}
    if phase == "ask":
        return vi_ask_line(hyp)
    if phase == "act":
        return vi_act_line(hyp)
    if phase == "judge":
        return vi_judge_line(
            hyp,
            assessment=str(reflection.get("assessment") or ""),
            next_question=str(reflection.get("next_question") or ""),
        )
    if phase.startswith("debate:"):
        actor_key = phase.split(":", 1)[-1]
        actor = {
            "gov": "Government AI",
            "corp": "Corporate AI",
            "society": "Societal AI",
            "chair": "Chair",
        }.get(actor_key, "Chair")
        return vi_debate_line(hyp, actor)
    if phase == "debate":
        return vi_debate_line(hyp, "Government AI")
    return vi_ask_line(hyp)
