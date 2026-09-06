"""ECONITH :: scientific 'what-if' hypothesis thinker (LLM-first).

Produces a short research dialogue instead of one-shot combinatorial slogans:

  1. ASK   — If I change these indicators..., what happens?
  2. ACT   — Apply the planned mutations / scenario.
  3. READ  — Observe macro deltas.
  4. JUDGE — Assess results and propose the next experiment.

Falls back to combinatorial generation when LLM is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from ai.simulator_engine.hypothesis_generator import (
    HypothesisGenerator,
    _country_name,
    _field_value,
    _live_codes,
    _MACRO_FIELDS,
)
from ai.simulator_engine.hypothesis_schema import Hypothesis, HypothesisOutcome
from ai.simulator_engine.operator_voice import language_name

logger = logging.getLogger("econith.world.hypothesis_thinker")


def _active_locale() -> str:
    try:
        from core.locale_prefs import dashboard_locale

        return dashboard_locale()
    except Exception:  # noqa: BLE001
        return "en"

_ALLOWED_FIELDS = frozenset(_MACRO_FIELDS)

# Rotate globally. Dashboard language (vi) must NOT pin every experiment to VNM.
_FOCUS_HUBS = (
    "USA",
    "CHN",
    "DEU",
    "JPN",
    "IND",
    "GBR",
    "FRA",
    "BRA",
    "VNM",
    "SAU",
)

# Light rotating curriculum so the research loop covers different mechanisms.
_CHAPTERS: tuple[tuple[str, str], ...] = (
    (
        "monetary",
        "Monetary chapter: probe interest_rate / inflation transmission in ONE hub.",
    ),
    (
        "fiscal_trade",
        "Fiscal & trade chapter: probe tax or tariff-style shocks and cross-border cost pass-through.",
    ),
    (
        "spillover",
        "Spillover chapter: shock country A but MEASURE country B (include both in subjects).",
    ),
    (
        "reversal",
        "Reversal chapter: partially reverse the strongest recent experiment to test symmetry.",
    ),
)


def chapter_for_run(run_index: int) -> tuple[str, str]:
    """Rotating curriculum chapter (name, instruction) for the given run count."""
    return _CHAPTERS[max(0, int(run_index)) % len(_CHAPTERS)]


def _with_direction(
    snapshot: dict[str, Any],
    country: str,
    field: str,
    value: float,
) -> dict[str, Any]:
    """Attach up/down/flat vs the current level so fingerprints stay meaningful."""
    current = _field_value(snapshot, country, field)
    direction = "flat"
    if current is not None:
        if value > current + 1e-9:
            direction = "up"
        elif value < current - 1e-9:
            direction = "down"
    return {
        "country": country,
        "field": field,
        "value": value,
        "direction": direction,
    }


def _extract_json(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    # Prefer fenced block
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def mutation_fingerprint(mutations: list[dict[str, Any]]) -> str:
    """Stable id of *what* was changed and in which direction (not exact value)."""
    parts: list[str] = []
    for m in mutations or []:
        if not isinstance(m, dict):
            continue
        country = str(m.get("country") or "?").upper()
        field = str(m.get("field") or "?")
        direction = str(m.get("direction") or "")
        parts.append(f"{country}.{field}{('/' + direction) if direction else ''}")
    return "|".join(sorted(parts)) or "(none)"


def signal_score(deltas: dict[str, float]) -> float:
    """How informative a cycle was: strongest |Δ| + breadth of movement."""
    vals = [abs(float(v)) for v in (deltas or {}).values() if isinstance(v, (int, float))]
    if not vals:
        return 0.0
    peak = max(vals)
    moved = sum(1 for v in vals if v > 1e-6)
    return round(min(1.0, peak * 10.0) * 0.7 + min(1.0, moved / 8.0) * 0.3, 4)


def _memory_path() -> Path:
    raw = (os.getenv("HYPOTHESIS_MEMORY_PATH") or "data/hypothesis_memory.json").strip()
    return Path(raw)


class ExperimentMemory:
    """Rolling, scored, persisted memory so each cycle learns from prior ones."""

    def __init__(
        self,
        *,
        maxlen: int = 8,
        path: Path | str | None = None,
        persist: bool = True,
    ) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._persist = persist
        self._path = Path(path) if path is not None else _memory_path()
        self._runs = 0
        if persist:
            self._load()

    # ---------------------------------------------------------------- persist
    def _load(self) -> None:
        try:
            if not self._path.exists():
                return
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # noqa: PERF203
            logger.debug("hypothesis memory unreadable: %s", self._path)
            return
        if not isinstance(data, dict):
            return
        self._runs = int(data.get("runs") or 0)
        for item in data.get("items") or []:
            if isinstance(item, dict):
                self._items.append(item)

    def _save(self) -> None:
        if not self._persist:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"runs": self._runs, "items": list(self._items)}
            self._path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            logger.debug("hypothesis memory not writable: %s", self._path)

    # ----------------------------------------------------------------- write
    def remember(
        self,
        *,
        question: str,
        mutations: list[dict[str, Any]],
        deltas: dict[str, float],
        assessment: str,
        next_question: str,
        chapter: str = "",
    ) -> None:
        muts = [m for m in (mutations or [])[:6] if isinstance(m, dict)]
        fingerprint = mutation_fingerprint(muts)
        score = signal_score(deltas)
        seen = sum(1 for it in self._items if it.get("fingerprint") == fingerprint)
        self._runs += 1
        self._items.append(
            {
                "question": question[:240],
                "mutations": muts,
                "top_deltas": dict(
                    sorted(
                        (deltas or {}).items(),
                        key=lambda kv: abs(float(kv[1])),
                        reverse=True,
                    )[:4]
                ),
                "assessment": assessment[:320],
                "next_question": next_question[:240],
                "fingerprint": fingerprint,
                "signal_score": score,
                "novelty": round(1.0 / (1.0 + seen), 3),
                "chapter": chapter or "",
            }
        )
        self._save()

    # ------------------------------------------------------------------ read
    @property
    def runs(self) -> int:
        return self._runs

    def as_prompt_block(self) -> str:
        if not self._items:
            return "(no prior experiments yet)"
        lines: list[str] = []
        for i, item in enumerate(self._items, 1):
            lines.append(
                f"{i}. Q: {item.get('question', '')}\n"
                f"   mutations: {item.get('mutations', [])}\n"
                f"   observed: {item.get('top_deltas', {})}\n"
                f"   signal_score: {item.get('signal_score', 0.0)} "
                f"novelty: {item.get('novelty', 1.0)} "
                f"chapter: {item.get('chapter') or '-'}\n"
                f"   assessment: {item.get('assessment', '')}\n"
                f"   next: {item.get('next_question', '')}"
            )
        return "\n".join(lines)

    def avoid_fingerprints(self, *, n: int = 4) -> list[str]:
        """Recently probed change-signatures the next cycle should not repeat."""
        out: list[str] = []
        for item in reversed(self._items):
            fp = str(item.get("fingerprint") or "")
            if fp and fp != "(none)" and fp not in out:
                out.append(fp)
            if len(out) >= n:
                break
        return out

    def under_explored_hubs(self, hubs: list[str], *, n: int = 5) -> list[str]:
        """Hubs with the least evidence so far (never tried first, then weak signal)."""
        touched: dict[str, float] = {}
        for item in self._items:
            score = float(item.get("signal_score") or 0.0)
            for m in item.get("mutations") or []:
                if isinstance(m, dict) and m.get("country"):
                    code = str(m["country"]).upper()
                    touched[code] = max(touched.get(code, 0.0), score)
        ranked = sorted(hubs, key=lambda c: (c in touched, touched.get(c, 0.0)))
        return ranked[:n]

    def strongest_recent(self) -> dict[str, Any] | None:
        """Highest-signal recent experiment (used by the 'reversal' chapter)."""
        best: dict[str, Any] | None = None
        for item in self._items:
            if not item.get("mutations"):
                continue
            if best is None or float(item.get("signal_score") or 0.0) > float(
                best.get("signal_score") or 0.0
            ):
                best = item
        return best

    def last_next_question(self) -> str:
        if not self._items:
            return ""
        return str(self._items[-1].get("next_question") or "")

    def recent_countries(self, *, n: int = 4) -> list[str]:
        """Most recent primary countries (newest first, de-duplicated)."""
        seen: list[str] = []
        for item in reversed(self._items):
            code = ""
            for m in item.get("mutations") or []:
                if isinstance(m, dict) and m.get("country"):
                    code = str(m["country"]).upper()
                    break
            if code and code not in seen:
                seen.append(code)
            if len(seen) >= n:
                break
        return seen

    def last_run_countries(self, *, n: int = 3) -> list[str]:
        """Primary country of each of the last n runs (may repeat)."""
        out: list[str] = []
        for item in reversed(self._items):
            for m in item.get("mutations") or []:
                if isinstance(m, dict) and m.get("country"):
                    out.append(str(m["country"]).upper())
                    break
            if len(out) >= n:
                break
        return out

    def avoid_countries(self) -> set[str]:
        """Avoid geographic lock-in (especially VNM when UI language is Vietnamese)."""
        runs = self.last_run_countries(n=3)
        avoid: set[str] = set()
        if len(runs) >= 2 and runs[0] == runs[1]:
            avoid.add(runs[0])
        if runs.count("VNM") >= 2:
            avoid.add("VNM")
        return avoid


class ScientificHypothesisThinker:
    """LLM research loop used by :class:`HypothesisRunner`."""

    def __init__(
        self,
        base: HypothesisGenerator,
        *,
        memory: ExperimentMemory | None = None,
    ) -> None:
        self._base = base
        self._memory = memory or ExperimentMemory()

    @property
    def memory(self) -> ExperimentMemory:
        return self._memory

    async def propose(self) -> Hypothesis:
        """Ask a grounded what-if; prefer LLM scientific JSON, else combinatorial."""
        snapshot = self._base._kernel.state_dict()
        codes = _live_codes(snapshot)
        if self._base._use_llm and self._base._llm_pool and codes:
            try:
                hyp = await asyncio.to_thread(self._propose_llm, snapshot, codes)
                if hyp is not None:
                    return hyp
            except Exception:  # noqa: BLE001
                logger.warning("scientific propose failed; combinatorial", exc_info=True)
        hyp = await self._base.generate_async()
        chapter, _ = chapter_for_run(self._memory.runs)
        plan = [
            _with_direction(
                snapshot,
                str(m.get("country") or ""),
                str(m.get("field") or ""),
                float(m.get("value") or 0.0),
            )
            for m in (hyp.mutations or [])
            if isinstance(m, dict) and m.get("value") is not None
        ]
        if plan:
            hyp = hyp.model_copy(update={"mutations": plan})
        # Wrap combinatorial as a question so the Agents feed still reads like research.
        if not hyp.rationale.startswith("ASK:"):
            ask = (
                f"If I change {plan[0]['country']}.{plan[0]['field']} → {plan[0]['value']}, "
                f"what happens?"
                if plan
                else (
                    f"If I apply «{hyp.prompt}», what happens to "
                    f"{', '.join(hyp.subjects) or 'the system'}?"
                )
            )
            hyp = hyp.model_copy(
                update={
                    "rationale": (
                        f"ASK: {ask}\n"
                        f"PLAN: mutations={plan}\n"
                        f"EXPECT: observe macro response"
                    ),
                    "generator": hyp.generator,
                    "question": ask[:280],
                }
            )
        return hyp.model_copy(
            update={
                "chapter": hyp.chapter or chapter,
                "fingerprint": hyp.fingerprint or mutation_fingerprint(plan),
                "independent_vars": hyp.independent_vars
                or [f"{m.get('country')}.{m.get('field')}" for m in plan if isinstance(m, dict)],
            }
        )

    def _propose_llm(self, snapshot: dict[str, Any], codes: list[str]) -> Hypothesis | None:
        hubs = [c for c in _FOCUS_HUBS if c in codes] or list(codes)
        # Shuffle so alphabetical order / VNM language bias does not dominate context.
        sample = list(hubs)
        self._base._rng.shuffle(sample)
        sample = sample[: min(8, len(sample))]
        lines: list[str] = []
        for code in sample:
            bits: list[str] = []
            for field in _MACRO_FIELDS:
                val = _field_value(snapshot, code, field)
                if val is not None:
                    bits.append(f"{field}={val:.4f}")
            lines.append(f"{code}({_country_name(snapshot, code)}): " + ", ".join(bits))
        context = "\n".join(lines)
        prior = self._memory.as_prompt_block()
        seed = self._memory.last_next_question()
        avoid = self._memory.avoid_countries()
        recent = self._memory.recent_countries(n=4)
        locale = _active_locale()
        lang = language_name(locale)
        prefer = [c for c in hubs if c not in avoid] or hubs
        prefer_txt = ", ".join(prefer[:6])
        avoid_txt = ", ".join(sorted(avoid)) if avoid else "(none)"
        seed_line = (
            f"Prior next-step hint (optional): {seed}\n"
            "You MAY pivot to a different country / spillover if the hint stays "
            "stuck on the same nation."
            if seed
            else "Invent a fresh, non-repeating experiment."
        )
        chapter, chapter_rule = chapter_for_run(self._memory.runs)
        under = self._memory.under_explored_hubs(prefer, n=4)
        avoid_fps = self._memory.avoid_fingerprints(n=4)
        if chapter == "spillover":
            focus_rule = (
                "Shock ONE country but include a trading partner in subjects and "
                "state the partner as the dependent side of the experiment."
            )
        elif chapter == "reversal":
            best = self._memory.strongest_recent()
            hint = (best or {}).get("mutations") or []
            focus_rule = (
                "Partially REVERSE this earlier high-signal experiment to test "
                f"symmetry: {hint}. Keep the same country/field, move the value "
                "back toward its prior level."
                if hint
                else f"Pick ONE primary subject from: {prefer_txt}."
            )
        else:
            focus_rule = (
                f"Pick ONE primary subject from the under-explored hubs: "
                f"{', '.join(under) or prefer_txt}. "
                "Optionally measure spillover on a second country."
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a GLOBAL economic research agent (not a Vietnam desk). "
                    "Design ONE controlled experiment. Reply with ONLY valid JSON:\n"
                    "{\n"
                    '  "question": "operator-facing what-if question",\n'
                    '  "prompt": "English imperative shock for the scenario parser ONLY",\n'
                    '  "mutations": [{"country":"DEU","field":"interest_rate","value":0.045}],\n'
                    '  "independent_vars": ["DEU.interest_rate"],\n'
                    '  "dependent_vars": ["FRA.gdp_growth","GBR.inflation","JPN.export"],\n'
                    '  "severity": 0.0-1.0,\n'
                    '  "category": "monetary_tighten|monetary_ease|stimulus|tariff|generic",\n'
                    '  "subjects": ["DEU","FRA","GBR"],\n'
                    '  "expected": "one sentence expected effect"\n'
                    "}\n"
                    f"Write question + expected in {lang} (UI language only). "
                    "Country choice is INDEPENDENT of UI language — do NOT default to "
                    "Vietnam/VNM just because the UI is Vietnamese. "
                    "Do NOT default every experiment to USA+CHN — rotate across ALL "
                    f"eligible hubs ({', '.join(hubs)}). "
                    "Keep prompt in English (machine parser). "
                    f"Allowed fields: {sorted(_ALLOWED_FIELDS)}. "
                    "Use AT MOST 2 mutations so the experiment stays controlled. "
                    "independent_vars = what you change; dependent_vars = what you "
                    "measure on OTHER hubs (global spillover — list 2-3 peers). "
                    "subjects MUST include the shocked hub PLUS at least two peers. "
                    "Use absolute target values close to current levels (small steps). "
                    "Do not invent countries outside the snapshot. "
                    f"Research chapter: {chapter_rule} "
                    f"{focus_rule} "
                    f"Avoid repeating these countries this cycle: {avoid_txt}. "
                    f"Recently used: {', '.join(recent) or 'none'}."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Dashboard language (for wording only): {lang}\n"
                    f"Eligible hubs: {', '.join(hubs)}\n"
                    f"Live macro snapshot:\n{context}\n\n"
                    f"Prior experiments (signal_score = how informative, "
                    f"novelty = how fresh):\n{prior}\n\n"
                    f"Do NOT repeat these change-signatures: "
                    f"{', '.join(avoid_fps) or '(none)'}\n"
                    f"Least-explored hubs (prefer these): "
                    f"{', '.join(under) or '(unknown)'}\n"
                    f"{seed_line}"
                ),
            },
        ]
        resp = self._base._llm_pool.create_chat_completion(
            base_url=self._base._llm_base_url or "http://localhost:11434/v1",
            model=self._base._llm_model or "llama3:8b",
            timeout=60.0,
            messages=messages,
            max_tokens=420,
            temperature=0.7,
        )
        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            content = str(resp)
        data = _extract_json(content)
        if not data:
            return None
        prompt = str(data.get("prompt") or "").strip()
        question = str(data.get("question") or "").strip()
        if len(prompt) < 12:
            return None
        if not question:
            question = f"If I apply «{prompt}», what happens to the macro state?"
        mutations = data.get("mutations") if isinstance(data.get("mutations"), list) else []
        clean_mut: list[dict[str, Any]] = []
        for m in mutations[:2]:
            if not isinstance(m, dict):
                continue
            country = str(m.get("country") or "").upper()
            field = str(m.get("field") or "")
            if country not in codes or field not in _ALLOWED_FIELDS:
                continue
            if country in avoid:
                continue
            try:
                value = float(m.get("value"))
            except (TypeError, ValueError):
                continue
            clean_mut.append(_with_direction(snapshot, country, field, value))
        # If LLM still clung to an avoided country, force a hub from prefer list.
        if not clean_mut and prefer:
            code = self._base._rng.choice(prefer)
            field = self._base._rng.choice(tuple(_ALLOWED_FIELDS))
            cur = _field_value(snapshot, code, field)
            if cur is None:
                return None
            step = max(abs(cur) * 0.08, 0.002)
            target = cur + step if self._base._rng.random() >= 0.5 else cur - step
            clean_mut = [_with_direction(snapshot, code, field, round(target, 6))]
            name = _country_name(snapshot, code)
            prompt = f"{name} adjusts {field} toward {target:.4f}"
            question = (
                f"Nếu tôi đổi {code}.{field} → {target:.4f}, ảnh hưởng lan ra toàn cầu thế nào?"
                if lang == "Vietnamese"
                else f"If I set {code}.{field} → {target:.4f}, what happens globally?"
            )
        subjects = [
            str(c).upper()
            for c in (data.get("subjects") or [])
            if str(c).upper() in codes and str(c).upper() not in avoid
        ]
        # Always anchor subjects on the shocked hubs first.
        for m in clean_mut:
            c = str(m["country"]).upper()
            if c not in subjects:
                subjects.insert(0, c)
        # Force 2+ peer observers from under-explored hubs (global, not USA+CHN only).
        peer_pool = [h for h in (under + prefer) if h not in subjects]
        self._base._rng.shuffle(peer_pool)
        for partner in peer_pool:
            if partner not in subjects:
                subjects.append(partner)
            if len(subjects) >= 3:
                break
        # If we somehow still have only the classic USA+CHN pair, inject a third hub.
        if set(subjects[:2]) == {"USA", "CHN"} and len(subjects) < 3:
            extra = next((h for h in prefer if h not in subjects), None)
            if extra:
                subjects.append(extra)
        subjects = subjects[:5]
        try:
            sev = float(data.get("severity", 0.55))
        except (TypeError, ValueError):
            sev = 0.55
        sev = max(0.0, min(1.0, sev))
        category = str(data.get("category") or "generic")
        expected = str(data.get("expected") or "").strip()
        indep = [
            str(v)[:48]
            for v in (data.get("independent_vars") or [])
            if isinstance(v, (str, int, float))
        ][:4]
        if not indep:
            indep = [f"{m['country']}.{m['field']}" for m in clean_mut]
        dep = [
            str(v)[:48]
            for v in (data.get("dependent_vars") or [])
            if isinstance(v, (str, int, float)) and str(v) not in indep
        ][:4]
        if lang == "Vietnamese":
            from ai.simulator_engine.hypothesis_i18n import vi_ask_line
            from ai.simulator_engine.operator_voice import (
                is_operator_locale,
                rewrite_line,
            )

            if question and not is_operator_locale(question, "vi"):
                question = rewrite_line(
                    self._base._llm_pool,
                    base_url=self._base._llm_base_url or "",
                    model=self._base._llm_model or "",
                    locale=locale,
                    source_text=question,
                    context="hypothesis question",
                ) or vi_ask_line(
                    Hypothesis(
                        id="tmp",
                        prompt=prompt,
                        subjects=subjects[:4],
                        mutations=clean_mut,
                    )
                )
            if expected and not is_operator_locale(expected, "vi"):
                expected = rewrite_line(
                    self._base._llm_pool,
                    base_url=self._base._llm_base_url or "",
                    model=self._base._llm_model or "",
                    locale=locale,
                    source_text=expected,
                    context="expected effect",
                ) or "Kỳ vọng lan tỏa sang tăng trưởng, lạm phát và thất nghiệp."
        rationale = (
            f"ASK: {question}\n"
            f"PLAN: mutations={clean_mut}\n"
            f"IV: {indep} DV: {dep} CHAPTER: {chapter}\n"
            f"EXPECT: {expected}"
        )
        return Hypothesis(
            id=uuid.uuid4().hex[:12],
            prompt=prompt,
            rationale=rationale[:800],
            severity=sev,
            category=category,
            subjects=subjects[:4],
            mutations=clean_mut,
            generator="llm",
            question=question[:280],
            independent_vars=indep,
            dependent_vars=dep,
            expected=expected[:280],
            chapter=chapter,
            fingerprint=mutation_fingerprint(clean_mut),
        )

    async def reflect(self, hyp: Hypothesis, outcome: HypothesisOutcome) -> dict[str, str]:
        """Judge results and propose the next question (LLM or heuristic)."""
        snapshot = self._base._kernel.state_dict()
        if self._base._use_llm and self._base._llm_pool and outcome.status == "ok":
            try:
                out = await asyncio.to_thread(self._reflect_llm, hyp, outcome, snapshot)
                if out:
                    self._memory.remember(
                        question=_ask_from_rationale(hyp.rationale) or hyp.prompt,
                        mutations=list(hyp.mutations or _mutations_from_rationale(hyp.rationale)),
                        deltas=dict(outcome.deltas or {}),
                        assessment=out.get("assessment", ""),
                        next_question=out.get("next_question", ""),
                        chapter=hyp.chapter,
                    )
                    return out
            except Exception:  # noqa: BLE001
                logger.warning("scientific reflect failed; heuristic", exc_info=True)
        out = _reflect_heuristic(hyp, outcome)
        out = await self._localize_reflection(out)
        self._memory.remember(
            question=_ask_from_rationale(hyp.rationale) or hyp.prompt,
            mutations=list(hyp.mutations or _mutations_from_rationale(hyp.rationale)),
            deltas=dict(outcome.deltas or {}),
            assessment=out.get("assessment", ""),
            next_question=out.get("next_question", ""),
            chapter=hyp.chapter,
        )
        return out

    async def _localize_reflection(self, out: dict[str, str]) -> dict[str, str]:
        """LLM-rewrite heuristic EN reflections into dashboard language."""
        locale = _active_locale()
        if not (locale or "").lower().startswith("vi"):
            return out
        from ai.simulator_engine.operator_voice import (
            is_operator_locale,
            rewrite_line,
        )

        assessment = out.get("assessment") or ""
        nxt = out.get("next_question") or ""
        vi_assessment = (
            "Đánh giá: thí nghiệm đã chạy; tín hiệu vĩ mô đủ để xoay hướng bước tiếp."
        )
        vi_next = (
            "Nếu tôi thay đổi biến số ở một hub khác hoặc mở lan tỏa thương mại, "
            "tăng trưởng và thất nghiệp đổi thế nào?"
        )

        if not (self._base._use_llm and self._base._llm_pool):
            return {
                "assessment": assessment
                if is_operator_locale(assessment, "vi")
                else vi_assessment,
                "next_question": nxt if is_operator_locale(nxt, "vi") else vi_next,
            }

        def _run() -> dict[str, str]:
            a = rewrite_line(
                self._base._llm_pool,
                base_url=self._base._llm_base_url or "",
                model=self._base._llm_model or "",
                locale=locale,
                source_text=assessment,
                context="hypothesis assessment",
            )
            n = rewrite_line(
                self._base._llm_pool,
                base_url=self._base._llm_base_url or "",
                model=self._base._llm_model or "",
                locale=locale,
                source_text=nxt,
                context="next experiment question",
            )
            return {
                "assessment": a
                if a and is_operator_locale(a, "vi")
                else (
                    assessment
                    if is_operator_locale(assessment, "vi")
                    else vi_assessment
                ),
                "next_question": n
                if n and is_operator_locale(n, "vi")
                else (nxt if is_operator_locale(nxt, "vi") else vi_next),
            }

        try:
            return await asyncio.to_thread(_run)
        except Exception:  # noqa: BLE001
            return {
                "assessment": assessment
                if is_operator_locale(assessment, "vi")
                else vi_assessment,
                "next_question": nxt if is_operator_locale(nxt, "vi") else vi_next,
            }

    def _reflect_llm(
        self,
        hyp: Hypothesis,
        outcome: HypothesisOutcome,
        snapshot: dict[str, Any],
    ) -> dict[str, str] | None:
        top = sorted(
            (outcome.deltas or {}).items(),
            key=lambda kv: abs(float(kv[1])),
            reverse=True,
        )[:6]
        delta_txt = ", ".join(f"{k}={v:+.5f}" for k, v in top) or "(no deltas)"
        ask = _ask_from_rationale(hyp.rationale) or hyp.prompt
        locale = _active_locale()
        lang = language_name(locale)
        messages = [
            {
                "role": "system",
                "content": (
                    "You are evaluating a completed macro experiment. "
                    "Reply ONLY JSON:\n"
                    "{\n"
                    '  "assessment": "What happened vs expectation (1-2 sentences)",\n'
                    '  "next_question": "If I change ... next, what happens?",\n'
                    '  "action_line": "Corporate desk narration of what was just applied (1 sentence)"\n'
                    "}\n"
                    f"Write assessment, next_question, and action_line in {lang} only. "
                    "action_line must be a full sentence for business operators — "
                    "NOT a bare key like USA.interest_rate→0.04. "
                    "Keep ISO codes/numbers. "
                    "For next_question, ROTATE geography: prefer a different hub "
                    "(USA/CHN/DEU/JPN/IND/…) or a cross-border spillover — "
                    "do not stay locked on Vietnam just because the UI is Vietnamese."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Dashboard language: {lang}\n"
                    f"Question asked: {ask}\n"
                    f"Action / prompt: {hyp.prompt}\n"
                    f"Subjects: {hyp.subjects}\n"
                    f"Observed deltas: {delta_txt}\n"
                    f"Micro: {outcome.micro_summary}\n"
                    "Assess and propose the next controlled experiment question "
                    "on a DIFFERENT country or trade link when possible."
                ),
            },
        ]
        resp = self._base._llm_pool.create_chat_completion(
            base_url=self._base._llm_base_url or "http://localhost:11434/v1",
            model=self._base._llm_model or "llama3:8b",
            timeout=60.0,
            messages=messages,
            max_tokens=220,
            temperature=0.5,
        )
        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError, TypeError):
            content = str(resp)
        data = _extract_json(content)
        if not data:
            return None
        assessment = str(data.get("assessment") or "").strip()
        nxt = str(data.get("next_question") or "").strip()
        if not assessment:
            return None
        if not nxt:
            nxt = f"If I push further on {hyp.subjects[0] if hyp.subjects else 'the system'}, what happens next?"
        action_line = str(data.get("action_line") or "").strip()
        out = {
            "assessment": assessment[:400],
            "next_question": nxt[:280],
        }
        if action_line:
            out["action_line"] = action_line[:280]
        # Force Vietnamese if dashboard locale is vi but model slipped into English.
        if lang == "Vietnamese":
            from ai.simulator_engine.hypothesis_i18n import vi_act_line, vi_ask_line, vi_judge_line
            from ai.simulator_engine.operator_voice import (
                is_operator_locale,
                rewrite_line,
            )

            defaults = {
                "assessment": vi_judge_line(hyp),
                "next_question": vi_ask_line(hyp),
                "action_line": vi_act_line(hyp),
            }
            for key in ("assessment", "next_question", "action_line"):
                val = out.get(key) or ""
                if not val:
                    continue
                if is_operator_locale(val, "vi"):
                    continue
                rewritten = rewrite_line(
                    self._base._llm_pool,
                    base_url=self._base._llm_base_url or "",
                    model=self._base._llm_model or "",
                    locale=locale,
                    source_text=val,
                    context=key,
                )
                out[key] = (
                    rewritten
                    if rewritten and is_operator_locale(rewritten, "vi")
                    else defaults[key]
                )
        return out


async def compose_dialogue_lines(
    thinker: "ScientificHypothesisThinker",
    hyp: Hypothesis,
    outcome: HypothesisOutcome,
    reflection: dict[str, str],
    *,
    locale: str = "en",
) -> list[dict[str, str]]:
    """LLM-authored ask/act/judge; falls back to structured lines."""
    base = thinker._base
    if base._use_llm and base._llm_pool:
        try:
            lines = await asyncio.to_thread(
                _compose_dialogue_llm, thinker, hyp, outcome, reflection, locale
            )
            if lines:
                return lines
        except Exception:  # noqa: BLE001
            logger.warning("dialogue compose failed; fallback", exc_info=True)
    return dialogue_lines_for_cycle(hyp, outcome, reflection, locale=locale)


def _compose_dialogue_llm(
    thinker: "ScientificHypothesisThinker",
    hyp: Hypothesis,
    outcome: HypothesisOutcome,
    reflection: dict[str, str],
    locale: str,
) -> list[dict[str, str]] | None:
    from ai.simulator_engine.hypothesis_i18n import vi_act_line, vi_ask_line, vi_judge_line
    from ai.simulator_engine.operator_voice import (
        ensure_locale_text,
        is_operator_locale,
        language_name,
    )

    lang = language_name(locale)
    ask0 = _ask_from_rationale(hyp.rationale) or hyp.prompt
    plan = list(hyp.mutations or _mutations_from_rationale(hyp.rationale))
    plan_txt = _format_mutations(plan)
    top = sorted(
        (outcome.deltas or {}).items(),
        key=lambda kv: abs(float(kv[1])),
        reverse=True,
    )[:4]
    delta_txt = ", ".join(f"{k}={v:+.4f}" for k, v in top) or "(none)"
    messages = [
        {
            "role": "system",
            "content": (
                "You write THREE short agent chat lines for an economic simulator. "
                "Reply ONLY JSON:\n"
                "{\n"
                '  "ask": "the what-if question only",\n'
                '  "act": "what was applied / measured (full sentence)",\n'
                '  "judge": "assessment + next direction"\n'
                "}\n"
                f"CRITICAL: all three strings MUST be natural {lang}. "
                "Do NOT prefix with actor names like 'Government AI:'. "
                "Never output bare tokens like USA.interest_rate→0.04 as the whole act line — "
                "embed codes inside a full sentence. Keep ISO codes and numbers."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Language required: {lang}\n"
                f"Seed ask: {ask0}\n"
                f"Prompt: {hyp.prompt}\n"
                f"Mutations: {plan_txt or plan}\n"
                f"Applied note: {reflection.get('action_line') or ''}\n"
                f"Assessment: {reflection.get('assessment') or ''}\n"
                f"Next: {reflection.get('next_question') or ''}\n"
                f"Deltas: {delta_txt}\n"
                f"Status: {outcome.status}"
            ),
        },
    ]
    resp = thinker._base._llm_pool.create_chat_completion(
        base_url=thinker._base._llm_base_url or "http://localhost:11434/v1",
        model=thinker._base._llm_model or "llama3:8b",
        timeout=60.0,
        messages=messages,
        max_tokens=320,
        temperature=0.45,
    )
    try:
        content = resp.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        content = str(resp)
    data = _extract_json(content)
    if not data:
        return None
    ask = str(data.get("ask") or "").strip()
    act = str(data.get("act") or "").strip()
    judge = str(data.get("judge") or "").strip()
    if not (ask and act and judge):
        return None
    # Reject act that is still just a mutation token.
    if "→" in act and len(act) < 40 and "." in act and " " not in act.strip():
        return None
    pool = thinker._base._llm_pool
    base_url = thinker._base._llm_base_url or ""
    model = thinker._base._llm_model or ""
    ask_fb = vi_ask_line(hyp)
    act_fb = vi_act_line(hyp)
    judge_fb = vi_judge_line(
        hyp,
        assessment=str(reflection.get("assessment") or ""),
        next_question=str(reflection.get("next_question") or ""),
    )
    ask = ensure_locale_text(
        pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=ask,
        context="ask",
        fallback=ask_fb if (locale or "").lower().startswith("vi") else None,
    )
    act = ensure_locale_text(
        pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=act,
        context="act",
        fallback=act_fb if (locale or "").lower().startswith("vi") else None,
    )
    judge = ensure_locale_text(
        pool,
        base_url=base_url,
        model=model,
        locale=locale,
        text=judge,
        context="judge",
        fallback=judge_fb if (locale or "").lower().startswith("vi") else None,
    )
    if (locale or "").lower().startswith("vi"):
        if not is_operator_locale(ask, "vi"):
            ask = ask_fb
        if not is_operator_locale(act, "vi"):
            act = act_fb
        if not is_operator_locale(judge, "vi"):
            judge = judge_fb
        if not all(is_operator_locale(t, "vi") for t in (ask, act, judge)):
            return None
    country = _dialogue_country(hyp)
    return [
        {"actor": "Government AI", "country": country, "text": ask[:360], "phase": "ask"},
        {"actor": "Corporate AI", "country": country, "text": act[:360], "phase": "act"},
        {"actor": "Societal AI", "country": country, "text": judge[:420], "phase": "judge"},
    ]


def _ask_from_rationale(rationale: str) -> str:
    for line in (rationale or "").splitlines():
        if line.startswith("ASK:"):
            return line[4:].strip()
    return ""


def _mutations_from_rationale(rationale: str) -> list[dict[str, Any]]:
    for line in (rationale or "").splitlines():
        if line.startswith("PLAN:"):
            raw = line.split("mutations=", 1)[-1].strip()
            try:
                data = json.loads(raw.replace("'", '"'))
                if isinstance(data, list):
                    return [m for m in data if isinstance(m, dict)][:6]
            except json.JSONDecodeError:
                return []
    return []


def _reflect_heuristic(hyp: Hypothesis, outcome: HypothesisOutcome) -> dict[str, str]:
    if outcome.status != "ok":
        return {
            "assessment": f"Experiment failed ({outcome.status}): {outcome.error or 'unknown'}",
            "next_question": "If I retry with a milder severity on the same subject, what happens?",
        }
    top = sorted(
        (outcome.deltas or {}).items(),
        key=lambda kv: abs(float(kv[1])),
        reverse=True,
    )
    if not top:
        assessment = (
            f"Applied «{hyp.prompt}» but macro deltas were near zero — "
            "the shock may be too mild or absorbed."
        )
        nxt = "If I increase the severity of the same shock, what happens?"
    else:
        k, v = top[0]
        assessment = (
            f"After «{hyp.prompt}», strongest move was {k} {v:+.4f}. "
            "The system reacted; signal is usable for the next step."
        )
        code = k.split(".", 1)[0] if "." in k else (hyp.subjects[0] if hyp.subjects else "USA")
        field = k.split(".", 1)[1] if "." in k else "interest_rate"
        direction = "further increase" if v >= 0 else "partial reverse"
        nxt = f"If I {direction} {field} in {code}, what happens to growth and unemployment?"
    return {"assessment": assessment, "next_question": nxt}


def dialogue_lines_for_cycle(
    hyp: Hypothesis,
    outcome: HypothesisOutcome,
    reflection: dict[str, str],
    *,
    locale: str = "en",
) -> list[dict[str, str]]:
    """Fallback ask/act/judge lines when LLM dialogue compose is unavailable."""
    from ai.simulator_engine.hypothesis_i18n import vi_act_line, vi_ask_line, vi_judge_line
    from ai.simulator_engine.operator_voice import is_operator_locale

    vi = (locale or "en").lower().startswith("vi")
    ask = _ask_from_rationale(hyp.rationale) or hyp.prompt
    plan = list(hyp.mutations or _mutations_from_rationale(hyp.rationale))
    country = _dialogue_country(hyp)
    plan_txt = _format_mutations(plan)
    assessment = (reflection.get("assessment") or "").strip()
    nxt = (reflection.get("next_question") or "").strip()
    act = reflection.get("action_line") or _fallback_act_line(hyp, plan, plan_txt, locale)
    judge = " ".join(p for p in (assessment, nxt) if p).strip() or assessment or nxt
    if vi:
        if not is_operator_locale(ask, "vi"):
            ask = vi_ask_line(hyp)
        if not is_operator_locale(act, "vi"):
            act = vi_act_line(hyp)
        if not is_operator_locale(judge, "vi"):
            judge = vi_judge_line(hyp, assessment=assessment, next_question=nxt)
    return [
        {"actor": "Government AI", "country": country, "text": ask, "phase": "ask"},
        {"actor": "Corporate AI", "country": country, "text": act, "phase": "act"},
        {"actor": "Societal AI", "country": country, "text": judge, "phase": "judge"},
    ]


def _dialogue_country(hyp: Hypothesis) -> str:
    if len(hyp.subjects) >= 2:
        return "+".join(hyp.subjects[:3])
    if hyp.subjects:
        return hyp.subjects[0].upper()
    return "GLOBAL"


def _fallback_act_line(
    hyp: Hypothesis,
    plan: list[dict[str, Any]],
    plan_txt: str,
    locale: str,
) -> str:
    vi = (locale or "en").lower().startswith("vi")
    if plan:
        m = plan[0]
        code = m.get("country")
        field = m.get("field")
        value = m.get("value")
        if vi:
            return (
                f"Doanh nghiệp tiến hành điều chỉnh: đặt {code}.{field} = {value}. "
                f"Theo dõi lan tỏa sang tăng trưởng, thất nghiệp, lạm phát"
                f"{' và các đối tác ' + ','.join(hyp.subjects[1:3]) if len(hyp.subjects) > 1 else ''}."
            )
        return (
            f"Corporate desk applies {code}.{field} = {value} and monitors "
            f"spillover into growth, unemployment, and inflation."
        )
    if vi:
        return f"Doanh nghiệp thực thi kịch bản «{hyp.prompt}» và đo phản ứng thị trường."
    return f"Corporate desk executes «{hyp.prompt}» and measures market response."


def _format_mutations(mutations: list[dict[str, Any]]) -> str:
    if not mutations:
        return ""
    bits: list[str] = []
    for m in mutations[:4]:
        bits.append(f"{m.get('country')}.{m.get('field')}→{m.get('value')}")
    return ", ".join(bits)
