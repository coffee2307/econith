"""ECONITH :: ai.simulator_engine.hypothesis_generator

Self-authors natural-language shock prompts from live World state.

Prefer combinatorial templates over current macro fields (weak-PC safe).
Optional LLM via ``HYPOTHESIS_USE_LLM=true`` (Ollama first) — never blocks the
asyncio loop (runs in a worker thread).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import uuid
from typing import Any, Protocol

from ai.simulator_engine.hypothesis_schema import Hypothesis

logger = logging.getLogger("econith.world.hypothesis_generator")

# Fields the scenario parser understands (logical names).
_MACRO_FIELDS = (
    "interest_rate",
    "inflation",
    "gdp_growth",
    "unemployment",
    "tax",
    "defense",
)

_SEVERITY_WORDS = (
    (0.35, "slight"),
    (0.55, "moderate"),
    (0.75, "significant"),
    (0.9, "major"),
)

_DIRECTION = {
    "interest_rate": ("raises interest rates", "cuts interest rates"),
    "inflation": ("faces a sharp inflation spike", "sees inflation ease"),
    "gdp_growth": ("suffers a growth contraction", "posts stronger growth"),
    "unemployment": ("sees unemployment surge", "sees unemployment fall"),
    "tax": ("hikes corporate tax", "cuts corporate tax"),
    "defense": ("raises defense spending", "cuts defense spending"),
}


class _WorldSnapshot(Protocol):
    def state_dict(self) -> dict[str, Any]: ...


def _env_use_llm() -> bool:
    raw = (os.getenv("HYPOTHESIS_USE_LLM") or "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _live_codes(snapshot: dict[str, Any]) -> list[str]:
    countries = snapshot.get("countries") or {}
    return sorted(str(c) for c in countries.keys())


def _country_name(snapshot: dict[str, Any], code: str) -> str:
    countries = snapshot.get("countries") or {}
    row = countries.get(code) or {}
    if isinstance(row, dict):
        return str(row.get("name") or code)
    return code


def _field_value(snapshot: dict[str, Any], code: str, field: str) -> float | None:
    countries = snapshot.get("countries") or {}
    row = countries.get(code)
    if not isinstance(row, dict):
        return None
    if field in row and isinstance(row[field], (int, float)):
        return float(row[field])
    vectors = row.get("vectors") or {}
    if field == "interest_rate":
        mon = vectors.get("monetary") or {}
        if isinstance(mon.get("interest_rate"), (int, float)):
            return float(mon["interest_rate"])
    if field == "inflation":
        mon = vectors.get("monetary") or {}
        if isinstance(mon.get("inflation_cpi"), (int, float)):
            return float(mon["inflation_cpi"])
    if field == "tax":
        fis = vectors.get("fiscal") or {}
        if isinstance(fis.get("corporate_tax"), (int, float)):
            return float(fis["corporate_tax"])
    if field == "unemployment":
        lab = vectors.get("labor") or {}
        if isinstance(lab.get("unemployment"), (int, float)):
            return float(lab["unemployment"])
    if field == "defense":
        geo = vectors.get("geopolitical") or {}
        if isinstance(geo.get("defense_spending_pct"), (int, float)):
            return float(geo["defense_spending_pct"])
    return None


def _severity_word(sev: float) -> str:
    word = "moderate"
    for threshold, label in _SEVERITY_WORDS:
        if sev <= threshold:
            return label
        word = label
    return word


def _parse_llm_prompt(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    for line in text.splitlines():
        cleaned = line.strip().strip("`\"'")
        if cleaned and not cleaned.lower().startswith("prompt"):
            return cleaned[:400]
    return text[:400]


class HypothesisGenerator:
    """Produce :class:`Hypothesis` instances grounded in live World state."""

    def __init__(
        self,
        kernel: _WorldSnapshot,
        *,
        llm_pool: Any | None = None,
        llm_base_url: str = "",
        llm_model: str = "",
        use_llm: bool | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._kernel = kernel
        self._llm_pool = llm_pool
        self._llm_base_url = llm_base_url
        self._llm_model = llm_model
        self._use_llm = _env_use_llm() if use_llm is None else bool(use_llm)
        self._rng = rng or random.Random()

    def generate(self) -> Hypothesis:
        """Sync path — combinatorial only (safe for tests / sync callers)."""
        snapshot = self._kernel.state_dict()
        codes = _live_codes(snapshot)
        if not codes:
            return Hypothesis(
                id=uuid.uuid4().hex[:12],
                prompt="Moderate global interest rate hike",
                rationale="empty world fallback",
                severity=0.5,
                category="monetary_tighten",
                subjects=[],
                generator="combinatorial",
            )
        return self._combinatorial(snapshot, codes)

    async def generate_async(self) -> Hypothesis:
        """Async path — optional LLM in a worker thread, else combinatorial."""
        snapshot = self._kernel.state_dict()
        codes = _live_codes(snapshot)
        if not codes:
            return self.generate()

        if self._use_llm and self._llm_pool:
            try:
                hyp = await asyncio.to_thread(self._from_llm, snapshot, codes)
                if hyp is not None:
                    return hyp
            except Exception:  # noqa: BLE001 — always fall back
                logger.warning(
                    "hypothesis LLM generation failed; using combinatorial",
                    exc_info=True,
                )

        return self._combinatorial(snapshot, codes)

    def _from_llm(self, snapshot: dict[str, Any], codes: list[str]) -> Hypothesis | None:
        sample = codes[:8]
        lines: list[str] = []
        for code in sample:
            bits: list[str] = []
            for field in _MACRO_FIELDS:
                val = _field_value(snapshot, code, field)
                if val is not None:
                    bits.append(f"{field}={val:.4f}")
            lines.append(f"{code}({_country_name(snapshot, code)}): " + ", ".join(bits))
        context = "\n".join(lines)
        messages = [
            {
                "role": "system",
                "content": (
                    "You author ONE short macro shock hypothesis for an economic "
                    "simulator. Reply with a single imperative sentence only. "
                    "Use country names or ISO codes from the context. Mention one "
                    "of: interest rates, inflation, growth, unemployment, tax, "
                    "defense spending, or tariffs. No climate, central-bank press "
                    "waffle, or social unrest headlines."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Live macro snapshot:\n{context}\n\n"
                    "Write one shock hypothesis sentence."
                ),
            },
        ]
        resp = self._llm_pool.create_chat_completion(
            base_url=self._llm_base_url or "http://localhost:11434/v1",
            model=self._llm_model or "llama3:8b",
            timeout=45.0,
            messages=messages,
            max_tokens=96,
            temperature=0.7,
        )
        content = ""
        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            content = str(resp)
        prompt = _parse_llm_prompt(content)
        if not prompt or len(prompt) < 12:
            return None
        low = prompt.lower()
        if any(tok in low for tok in ("climate crisis", "cpi waffle", "unrest spreads")):
            return None

        subjects = [
            c
            for c in codes
            if c.lower() in low or _country_name(snapshot, c).lower() in low
        ]
        sev = 0.55
        for word, val in (
            ("massive", 0.9),
            ("major", 0.72),
            ("significant", 0.7),
            ("moderate", 0.5),
            ("slight", 0.3),
        ):
            if word in low:
                sev = val
                break
        category = "generic"
        if "tariff" in low:
            category = "tariff"
        elif "interest" in low or "rate" in low:
            category = (
                "monetary_tighten"
                if any(w in low for w in ("hike", "raise", "tighten"))
                else "monetary_ease"
            )
        return Hypothesis(
            id=uuid.uuid4().hex[:12],
            prompt=prompt,
            rationale="llm_from_live_state",
            severity=sev,
            category=category,
            subjects=subjects[:4],
            generator="llm",
        )

    def _combinatorial(self, snapshot: dict[str, Any], codes: list[str]) -> Hypothesis:
        code = self._rng.choice(codes)
        name = _country_name(snapshot, code)
        field = self._rng.choice(_MACRO_FIELDS)
        sev = round(self._rng.uniform(0.28, 0.85), 3)
        word = _severity_word(sev)
        up = self._rng.random() >= 0.45
        phrase_up, phrase_down = _DIRECTION[field]
        action = phrase_up if up else phrase_down
        cur = _field_value(snapshot, code, field)
        rationale = f"state-derived {field}@{code}"
        if cur is not None:
            rationale = f"{rationale} current={cur:.4f}"

        if self._rng.random() < 0.22 and len(codes) >= 2:
            src, tgt = self._rng.sample(codes, 2)
            src_n = _country_name(snapshot, src)
            tgt_n = _country_name(snapshot, tgt)
            prompt = f"{src_n} imposes a {word} tariff barrier on {tgt_n}"
            return Hypothesis(
                id=uuid.uuid4().hex[:12],
                prompt=prompt,
                rationale=f"state-derived tariff {src}->{tgt}",
                severity=sev,
                category="tariff",
                subjects=[src, tgt],
                generator="combinatorial",
            )

        prompt = f"{name} {action} in a {word} move"
        category = {
            "interest_rate": "monetary_tighten" if up else "monetary_ease",
            "inflation": "generic",
            "gdp_growth": "generic",
            "unemployment": "stimulus" if not up else "generic",
            "tax": "stimulus" if not up else "generic",
            "defense": "conflict" if up else "generic",
        }.get(field, "generic")
        return Hypothesis(
            id=uuid.uuid4().hex[:12],
            prompt=prompt,
            rationale=rationale,
            severity=sev,
            category=category,
            subjects=[code],
            generator="combinatorial",
        )
