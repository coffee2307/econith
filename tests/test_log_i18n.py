"""Locale-pure Event Log lines for hypothesis / scenario."""
from __future__ import annotations

from ai.simulator_engine.hypothesis_schema import Hypothesis, HypothesisOutcome
from ai.simulator_engine.log_i18n import (
    format_hypothesis_log,
    format_scenario_headline,
    format_scenario_log,
    hypothesis_summary,
)


def test_hypothesis_log_vietnamese_no_english_wrapper() -> None:
    hyp = Hypothesis(
        id="abc123",
        prompt="USA raises interest rates in a moderate move",  # parse-only EN
        category="monetary_tighten",
        severity=0.55,
        subjects=["USA"],
    )
    outcome = HypothesisOutcome(
        hypothesis_id=hyp.id,
        status="ok",
        deltas={"USA.interest_rate": 0.01},
    )
    msg = format_hypothesis_log(hyp, outcome, "vi")
    assert msg.startswith("Giả thuyết abc123:")
    assert "biến động vĩ mô" in msg
    assert "Hypothesis" not in msg
    assert "macro deltas" not in msg
    # Structured VI summary — not the raw English parse prompt.
    assert "thắt chặt tiền tệ" in msg or "Hoa Kỳ" in msg


def test_hypothesis_log_english_keeps_prompt() -> None:
    hyp = Hypothesis(
        id="abc123",
        prompt="USA raises interest rates in a moderate move",
        category="monetary_tighten",
        severity=0.55,
        subjects=["USA"],
    )
    outcome = HypothesisOutcome(hypothesis_id=hyp.id, status="ok", deltas={})
    msg = format_hypothesis_log(hyp, outcome, "en")
    assert msg.startswith("Hypothesis abc123:")
    assert "USA raises interest rates" in msg


def test_scenario_headline_vi() -> None:
    line = format_scenario_headline("monetary_tighten", 0.5, ["Hoa Kỳ"], "vi")
    assert "Kịch bản" in line
    assert "thắt chặt tiền tệ" in line
    assert "scenario affecting" not in line


def test_scenario_log_vi() -> None:
    line = format_scenario_log("raise rates", "tóm tắt", "vĩ mô[x]", "vi")
    assert line.startswith("Kịch bản")
    assert "Tác động:" in line
    assert "Scenario" not in line
    assert "Effects:" not in line


def test_hypothesis_summary_tariff_vi() -> None:
    hyp = Hypothesis(
        id="t1",
        prompt="China imposes a major tariff barrier on Vietnam",
        category="tariff",
        severity=0.72,
        subjects=["CHN", "VNM"],
    )
    summary = hypothesis_summary(hyp, "vi")
    assert "Trung Quốc" in summary
    assert "Việt Nam" in summary
    assert "thuế quan" in summary
