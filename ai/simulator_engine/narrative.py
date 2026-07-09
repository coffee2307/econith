"""ECONITH :: ai.simulator_engine.narrative

Cybernetic Narrative & Global Event-Log Generator.

Every agent decision and every cross-impact translation is emitted as a typed
:class:`CausalFact` -- a structured (actor, cause, action, effect, metrics)
tuple. The :class:`NarrativeEngine` synthesises these into hyper-detailed,
context-aware news lines that explain the *why* behind each market and world
movement, closing the interpretability loop for the unified feedback system.

The engine is deterministic and dependency-free (template synthesis over the
structured facts). Swapping in a real LLM later means only replacing
``compose``'s body -- the :class:`CausalFact` schema is the stable contract.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

__all__ = ["CausalFact", "NarrativeEngine"]


@dataclass(slots=True)
class CausalFact:
    """A structured cause->effect record produced by an agent or the kernel."""

    actor: str                     # "Corporate AI" | "Government AI" | "Societal AI" | "Market"
    country: str                   # display name of the affected nation
    action: str                    # what the actor did
    cause: str                     # why (the trigger)
    effect: str                    # measured consequence
    level: str = "info"            # info | ok | warn | danger
    metrics: dict[str, float] = field(default_factory=dict)
    tags: tuple[str, ...] = ()     # e.g. ("capital_flight", "regime:VOLATILE")


class NarrativeEngine:
    """Synthesises :class:`CausalFact`s into rich, causal news lines."""

    # Connective phrasing keyed by severity, giving the feed tonal variety.
    _CONNECTORS: dict[str, tuple[str, ...]] = {
        "danger": (
            "As {cause}, {actor} in {country} moved decisively: {action}. {effect}.",
            "{cause} forced {actor}'s hand -- {country} saw {action}, and {effect}.",
            "Crisis dynamics ({cause}) drove {actor} to {action} across {country}; {effect}.",
        ),
        "warn": (
            "With {cause}, {actor} in {country} opted to {action} -- {effect}.",
            "{actor} responded to {cause} by choosing to {action} in {country}; {effect}.",
        ),
        "ok": (
            "Easing conditions ({cause}) let {actor} in {country} {action}; {effect}.",
            "{actor} in {country} took advantage of {cause} to {action} -- {effect}.",
        ),
        "info": (
            "{actor} in {country} {action} amid {cause}; {effect}.",
            "Against a backdrop of {cause}, {actor} in {country} {action} -- {effect}.",
        ),
    }

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)

    def compose(self, fact: CausalFact, *, locale: str = "en") -> str:
        """Render a single causal fact into a detailed news line."""
        if locale.lower().startswith("vi"):
            return self._compose_vi(fact)
        templates = self._CONNECTORS.get(fact.level, self._CONNECTORS["info"])
        template = self._rng.choice(templates)
        line = template.format(
            actor=fact.actor,
            country=fact.country,
            action=fact.action,
            cause=fact.cause,
            effect=fact.effect,
        )
        metric_suffix = self._format_metrics(fact.metrics)
        return f"{line}{metric_suffix}"

    _ACTOR_VI = {
        "Corporate AI": "AI doanh nghiệp",
        "Government AI": "AI chính phủ",
        "Societal AI": "AI xã hội",
        "Market": "Thị trường",
        "Sovereign": "Đại diện chủ quyền",
    }

    _PHRASE_VI = (
        ("systemic market-crisis print", "khủng hoảng thị trường"),
        ("sell-pressure", "áp lực bán"),
        ("repatriated", "hồi hương"),
        ("dumped sovereign paper", "bán trái phiếu"),
        ("relocated supply chains", "dời chuỗi cung ứng"),
        ("supply-chain friction rising", "ma sát chuỗi cung ứng tăng"),
        ("capital controls", "kiểm soát vốn"),
        ("imposed capital controls", "áp kiểm soát vốn"),
        ("yields", "lợi suất"),
        ("currency", "tỷ giá"),
    )

    def _compose_vi(self, fact: CausalFact) -> str:
        actor = self._ACTOR_VI.get(fact.actor, fact.actor)
        action = self._vi_phrase(fact.action)
        cause = self._vi_phrase(fact.cause)
        effect = self._vi_phrase(fact.effect)
        templates = {
            "danger": (
                "Vì {cause}, {actor} tại {country} hành động quyết liệt: {action}. {effect}.",
                "{cause} buộc {actor} tại {country} phải {action} — {effect}.",
            ),
            "warn": (
                "Trước {cause}, {actor} tại {country} chọn {action} — {effect}.",
                "{actor} tại {country} phản ứng với {cause} bằng {action}; {effect}.",
            ),
            "ok": (
                "Khi {cause} dịu đi, {actor} tại {country} {action}; {effect}.",
            ),
            "info": (
                "{actor} tại {country} {action} giữa bối cảnh {cause}; {effect}.",
            ),
        }
        pool = templates.get(fact.level, templates["info"])
        line = self._rng.choice(pool).format(
            actor=actor,
            country=fact.country,
            action=action,
            cause=cause,
            effect=effect,
        )
        metric_suffix = self._format_metrics(fact.metrics)
        return f"{line}{metric_suffix}"

    def _vi_phrase(self, text: str) -> str:
        out = text
        for en, vi in self._PHRASE_VI:
            out = out.replace(en, vi)
        return out

    def regime_transition(
        self, country_or_market: str, old: str, new: str, driver: str,
        confidence: float,
    ) -> CausalFact:
        """Build the canonical 'regime flipped, here's why' fact."""
        return CausalFact(
            actor="Market",
            country=country_or_market,
            action=f"AI market regime shifted {old} -> {new}",
            cause=driver,
            effect=(
                f"the HMM/GMM classifier re-weighted capital allocation at "
                f"{confidence*100:.0f}% conviction"
            ),
            level="danger" if new == "VOLATILE" else "warn" if new == "TRENDING" else "info",
            metrics={"confidence": round(confidence, 3)},
            tags=(f"regime:{new}",),
        )

    @staticmethod
    def _format_metrics(metrics: dict[str, float]) -> str:
        if not metrics:
            return ""
        parts: list[str] = []
        for key, value in metrics.items():
            parts.append(f"{key.replace('_', ' ')} {NarrativeEngine._fmt(key, value)}")
        return f" [{', '.join(parts)}]"

    @staticmethod
    def _fmt(key: str, value: float) -> str:
        k = key.lower()
        if "bps" in k:
            return f"{value:+.0f}bps"
        if "usd" in k or "capital" in k or "notional" in k:
            if abs(value) >= 1e9:
                return f"${value / 1e9:.2f}B"
            if abs(value) >= 1e6:
                return f"${value / 1e6:.1f}M"
            return f"${value:,.0f}"
        if "pct" in k or "rate" in k or "inflation" in k or "depreciation" in k:
            return f"{value * 100:+.2f}%"
        if "confidence" in k or "index" in k or "intensity" in k or "vol" in k:
            return f"{value:.2f}"
        return f"{value:+.3g}"
