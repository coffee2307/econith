"""ECONITH :: ai.simulator_engine.llm_scenario

Advanced LLM Scenario & Impact Engine (master plan, Phase 8).

Parses complex natural-language "what-if" commands -- including sci-fi and
extreme black swans -- into a **unified state mutation** captured by a Pydantic
structured output, :class:`ScenarioParse`. Crucially, the parse does not only
mutate macro variables: it also emits a
:class:`~ai.simulator_engine.cross_impact.MicrostructuralVolatilityVector` -- the
liquidity / order-flow shock signature the ECONITH Quant engine ingests off
``world.micro_impact``.

Example
-------
    "US imposes a massive global tariff barrier"
    ->  tariff_actions:  USA -> {every rival} @ ~65%
        macro:           USA CPI up, rivals' growth down
        micro:           volatility x3.4, OBI shock -0.72, VOLATILE regime
                         pressure (forces an HMM/GMM transition), 6-tick decay

Mock-first: a deterministic, category-aware rule parser handles the phrasing
with zero external dependencies. When an ``OPENAI_API_KEY`` is configured the
same :class:`ScenarioParse` schema can be produced by a real LLM call -- nothing
downstream changes.
"""
from __future__ import annotations

import logging
import os
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ai.simulator_engine.cross_impact import MicrostructuralVolatilityVector
from ai.simulator_engine.world_kernel import LOGICAL_FIELDS, WorldKernel
from core.event_bus import EventBus

logger = logging.getLogger("econith.world.llm_scenario")


# ===========================================================================
#  Structured output schema (Pydantic)
# ===========================================================================
class ScenarioCategory(str, Enum):
    TARIFF = "tariff"
    MONETARY_TIGHTEN = "monetary_tighten"
    MONETARY_EASE = "monetary_ease"
    SANCTIONS = "sanctions"
    CONFLICT = "conflict"
    PANDEMIC = "pandemic"
    TECH_BREAKTHROUGH = "tech_breakthrough"
    SOVEREIGN_DEFAULT = "sovereign_default"
    STIMULUS = "stimulus"
    GENERIC = "generic"


class MacroMutation(BaseModel):
    country: str
    field: str            # logical field name (see LOGICAL_FIELDS)
    value: float          # absolute target value


class TariffAction(BaseModel):
    source: str
    target: str
    value: float          # fractional tariff rate


class ScenarioParse(BaseModel):
    """The unified structured output of a parsed scenario command."""

    prompt: str
    category: ScenarioCategory
    severity: float = Field(ge=0.0, le=1.0)
    subjects: list[str] = Field(default_factory=list)   # affected country codes
    mutations: list[MacroMutation] = Field(default_factory=list)
    tariff_actions: list[TariffAction] = Field(default_factory=list)
    micro: MicrostructuralVolatilityVector = Field(
        default_factory=MicrostructuralVolatilityVector.neutral
    )
    narrative: str = ""


# ===========================================================================
#  Lexicons
# ===========================================================================
_COUNTRY_ALIASES: dict[str, str] = {
    "fed": "USA", "us": "USA", "u.s.": "USA", "usa": "USA", "america": "USA",
    "american": "USA", "washington": "USA",
    "china": "CHN", "chinese": "CHN", "beijing": "CHN", "prc": "CHN",
    "vietnam": "VNM", "vnm": "VNM", "hanoi": "VNM",
    "japan": "JPN", "japanese": "JPN", "jpn": "JPN", "tokyo": "JPN", "boj": "JPN",
    "india": "IND", "indian": "IND", "ind": "IND", "delhi": "IND", "rbi": "IND",
    "germany": "DEU", "german": "DEU", "deu": "DEU", "europe": "DEU",
    "ecb": "DEU", "eu": "DEU", "berlin": "DEU", "brussels": "DEU",
}
_GLOBAL_TOKENS = ("global", "worldwide", "world", "everyone", "all nations",
                  "all countries", "the world", "planet")

_FIELD_ALIASES: dict[str, str] = {
    "rate": "interest_rate", "interest": "interest_rate", "rates": "interest_rate",
    "inflation": "inflation", "cpi": "inflation",
    "tax": "tax", "corporate tax": "tax",
    "gdp": "gdp_growth", "growth": "gdp_growth",
    "unemployment": "unemployment", "jobless": "unemployment",
    "defense": "defense", "defence": "defense", "military": "defense",
}

_SEVERITY_WORDS: dict[str, float] = {
    "total": 0.95, "massive": 0.9, "extreme": 0.9, "catastrophic": 0.95,
    "severe": 0.85, "huge": 0.85, "unprecedented": 0.9,
    "major": 0.72, "large": 0.7, "significant": 0.7, "sharp": 0.72,
    "moderate": 0.5, "modest": 0.45,
    "slight": 0.3, "minor": 0.28, "small": 0.3, "mild": 0.32,
}


class LLMScenarioEngine:
    def __init__(self, bus: EventBus, kernel: WorldKernel) -> None:
        self._bus = bus
        self._kernel = kernel
        self._llm_ready = bool(os.getenv("OPENAI_API_KEY"))

    # -- parsing --------------------------------------------------------------
    def parse(self, prompt: str) -> ScenarioParse:
        """Parse a NL command into the unified structured output.

        Heuristic, category-aware parser (LLM swap-in point). Always returns a
        valid :class:`ScenarioParse`, including a microstructural shock vector.
        """
        text = prompt.lower().strip()
        category = self._classify(text)
        severity = self._severity(text)
        subjects = self._subjects(text)

        mutations = self._macro_mutations(text, category, severity, subjects)
        tariffs = self._tariff_actions(text, category, severity, subjects)
        micro = self._micro_vector(category, severity)
        narrative = self._headline(category, severity, subjects)
        micro.headline = narrative

        return ScenarioParse(
            prompt=prompt,
            category=category,
            severity=round(severity, 3),
            subjects=subjects,
            mutations=mutations,
            tariff_actions=tariffs,
            micro=micro,
            narrative=narrative,
        )

    # -- classification helpers ----------------------------------------------
    @staticmethod
    def _classify(text: str) -> ScenarioCategory:
        def has(*words: str) -> bool:
            return any(re.search(rf"\b{re.escape(w)}\b", text) for w in words)

        if has("tariff", "trade barrier", "trade war", "import duty", "duties",
               "protectionism", "embargo"):
            return ScenarioCategory.TARIFF
        if has("sanction", "sanctions", "freeze assets", "swift", "blockade"):
            return ScenarioCategory.SANCTIONS
        if has("war", "invasion", "invade", "conflict", "missile", "military strike",
               "mobiliz", "mobilis"):
            return ScenarioCategory.CONFLICT
        if has("pandemic", "virus", "plague", "outbreak", "lockdown", "quarantine"):
            return ScenarioCategory.PANDEMIC
        if has("breakthrough", "agi", "fusion", "superintelligence", "quantum",
               "miracle", "discovery", "innovation boom"):
            return ScenarioCategory.TECH_BREAKTHROUGH
        if has("default", "bankrupt", "insolvency", "debt crisis", "collapse"):
            return ScenarioCategory.SOVEREIGN_DEFAULT
        if has("stimulus", "bailout", "spending package", "helicopter money", "qe"):
            return ScenarioCategory.STIMULUS
        if has("hike", "raise rate", "raises rate", "tighten", "hawkish"):
            return ScenarioCategory.MONETARY_TIGHTEN
        if has("cut rate", "cuts rate", "ease", "dovish", "lower rate"):
            return ScenarioCategory.MONETARY_EASE
        # rate + number without an explicit verb -> infer direction later
        if re.search(r"\brate", text):
            return ScenarioCategory.MONETARY_TIGHTEN
        return ScenarioCategory.GENERIC

    @staticmethod
    def _severity(text: str) -> float:
        for word, val in _SEVERITY_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", text):
                return val
        # scale by an explicit percentage if present (e.g. 200% -> saturate)
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if m:
            pct = float(m.group(1))
            return max(0.3, min(1.0, pct / 100.0))
        return 0.6

    def _subjects(self, text: str) -> list[str]:
        if any(tok in text for tok in _GLOBAL_TOKENS):
            return list(self._kernel.world.codes())
        found: list[str] = []
        for token, code in _COUNTRY_ALIASES.items():
            if re.search(rf"\b{re.escape(token)}\b", text) and code not in found:
                if code in self._kernel.world.countries:
                    found.append(code)
        return found or ["USA"]

    @staticmethod
    def _explicit_value(text: str) -> float | None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        if not m:
            m = re.search(r"\bto\s+(\d+(?:\.\d+)?)\b", text)
        if m:
            raw = float(m.group(1))
            return raw / 100.0 if raw > 1.0 else raw
        return None

    def _current(self, code: str, logical: str) -> float:
        c = self._kernel.world.countries.get(code)
        if c is None or logical not in LOGICAL_FIELDS:
            return 0.0
        group, real = LOGICAL_FIELDS[logical]
        if group is None:
            return float(getattr(c, real, 0.0))
        return float(c.get_field(group, real) or 0.0)

    # -- structured builders --------------------------------------------------
    def _macro_mutations(
        self, text: str, category: ScenarioCategory, sev: float, subjects: list[str],
    ) -> list[MacroMutation]:
        muts: list[MacroMutation] = []
        explicit = self._explicit_value(text)

        for code in subjects:
            if category is ScenarioCategory.MONETARY_TIGHTEN:
                target = explicit if explicit is not None else self._current(code, "interest_rate") + 0.02 * sev
                muts.append(MacroMutation(country=code, field="interest_rate", value=target))
            elif category is ScenarioCategory.MONETARY_EASE:
                target = explicit if explicit is not None else max(0.0, self._current(code, "interest_rate") - 0.02 * sev)
                muts.append(MacroMutation(country=code, field="interest_rate", value=target))
            elif category is ScenarioCategory.CONFLICT:
                muts.append(MacroMutation(country=code, field="defense",
                                          value=self._current(code, "defense") + 0.03 * sev))
                muts.append(MacroMutation(country=code, field="gdp_growth",
                                          value=self._current(code, "gdp_growth") - 0.05 * sev))
            elif category is ScenarioCategory.PANDEMIC:
                muts.append(MacroMutation(country=code, field="unemployment",
                                          value=self._current(code, "unemployment") + 0.05 * sev))
                muts.append(MacroMutation(country=code, field="gdp_growth",
                                          value=self._current(code, "gdp_growth") - 0.06 * sev))
            elif category is ScenarioCategory.TECH_BREAKTHROUGH:
                muts.append(MacroMutation(country=code, field="gdp_growth",
                                          value=self._current(code, "gdp_growth") + 0.05 * sev))
            elif category is ScenarioCategory.STIMULUS:
                muts.append(MacroMutation(country=code, field="gdp_growth",
                                          value=self._current(code, "gdp_growth") + 0.03 * sev))
                muts.append(MacroMutation(country=code, field="inflation",
                                          value=self._current(code, "inflation") + 0.02 * sev))
            elif category is ScenarioCategory.SOVEREIGN_DEFAULT:
                muts.append(MacroMutation(country=code, field="interest_rate",
                                          value=self._current(code, "interest_rate") + 0.05 * sev))
                muts.append(MacroMutation(country=code, field="gdp_growth",
                                          value=self._current(code, "gdp_growth") - 0.07 * sev))
            elif category is ScenarioCategory.GENERIC and explicit is not None:
                field = self._detect_field(text)
                muts.append(MacroMutation(country=code, field=field, value=explicit))
        return muts

    def _tariff_actions(
        self, text: str, category: ScenarioCategory, sev: float, subjects: list[str],
    ) -> list[TariffAction]:
        if category not in (ScenarioCategory.TARIFF, ScenarioCategory.SANCTIONS):
            return []
        explicit = self._explicit_value(text)
        rate = explicit if explicit is not None else min(0.9, 0.1 + 0.6 * sev)

        all_codes = list(self._kernel.world.codes())
        actions: list[TariffAction] = []
        # "global barrier": the imposer(s) tariff every other nation.
        is_global = any(tok in text for tok in _GLOBAL_TOKENS)
        for src in subjects:
            targets = [c for c in all_codes if c != src] if is_global else \
                [c for c in subjects if c != src] or [c for c in all_codes if c != src][:1]
            for dst in targets:
                actions.append(TariffAction(source=src, target=dst, value=round(rate, 3)))
        return actions

    @staticmethod
    def _detect_field(text: str) -> str:
        for token, mapped in _FIELD_ALIASES.items():
            if re.search(rf"\b{re.escape(token)}\b", text):
                return mapped
        return "interest_rate"

    def _micro_vector(
        self, category: ScenarioCategory, sev: float
    ) -> MicrostructuralVolatilityVector:
        """Category-conditioned microstructural shock signature."""
        risk_off = {
            ScenarioCategory.TARIFF, ScenarioCategory.SANCTIONS,
            ScenarioCategory.CONFLICT, ScenarioCategory.PANDEMIC,
            ScenarioCategory.SOVEREIGN_DEFAULT,
        }
        risk_on = {
            ScenarioCategory.MONETARY_EASE, ScenarioCategory.STIMULUS,
            ScenarioCategory.TECH_BREAKTHROUGH,
        }
        if category in risk_off:
            return MicrostructuralVolatilityVector(
                volatility_multiplier=1.0 + 2.6 * sev,
                order_flow_shock=-0.8 * sev,
                liquidity_drain=0.55 * sev,
                spread_widening_bps=45.0 * sev,
                regime_pressure={"VOLATILE": 2.4 * sev, "CALM": -1.8 * sev,
                                 "TRENDING": 0.5 * sev, "MEAN_REVERTING": 0.0},
                duration_ticks=7,
                origin=f"scenario:{category.value}",
            )
        if category in risk_on:
            return MicrostructuralVolatilityVector(
                volatility_multiplier=1.0 + 0.7 * sev,
                order_flow_shock=0.55 * sev,
                liquidity_drain=0.0,
                spread_widening_bps=5.0 * sev,
                regime_pressure={"TRENDING": 1.8 * sev, "CALM": 0.3 * sev,
                                 "VOLATILE": -0.4 * sev, "MEAN_REVERTING": 0.0},
                duration_ticks=5,
                origin=f"scenario:{category.value}",
            )
        if category is ScenarioCategory.MONETARY_TIGHTEN:
            return MicrostructuralVolatilityVector(
                volatility_multiplier=1.0 + 1.2 * sev,
                order_flow_shock=-0.4 * sev,
                liquidity_drain=0.2 * sev,
                spread_widening_bps=15.0 * sev,
                regime_pressure={"VOLATILE": 1.0 * sev, "TRENDING": 0.8 * sev,
                                 "CALM": -0.9 * sev, "MEAN_REVERTING": 0.0},
                duration_ticks=5,
                origin=f"scenario:{category.value}",
            )
        return MicrostructuralVolatilityVector(
            volatility_multiplier=1.0 + 0.5 * sev,
            order_flow_shock=-0.2 * sev,
            regime_pressure={"VOLATILE": 0.5 * sev, "CALM": -0.4 * sev,
                             "TRENDING": 0.0, "MEAN_REVERTING": 0.2},
            duration_ticks=3,
            origin=f"scenario:{category.value}",
        )

    def _headline(self, category: ScenarioCategory, sev: float, subjects: list[str]) -> str:
        from core.locale_prefs import dashboard_locale
        from ai.simulator_engine.log_i18n import format_scenario_headline

        names = [
            self._kernel.world.countries[c].name
            for c in subjects
            if c in self._kernel.world.countries
        ]
        # Prefer localized place names when UI is Vietnamese.
        locale = dashboard_locale()
        if locale.lower().startswith("vi"):
            from ai.simulator_engine.narrative import NarrativeEngine

            ne = NarrativeEngine()
            names = [
                ne._place_vi(c)  # noqa: SLF001
                for c in subjects
                if c in self._kernel.world.countries
            ] or names
        return format_scenario_headline(category.value, sev, names, locale)

    # -- orchestration --------------------------------------------------------
    async def run_scenario(
        self, prompt: str, *, announce: bool = True
    ) -> dict[str, Any]:
        """Parse, apply the unified mutation, inject the micro shock, announce.

        ``announce=False`` still mutates + publishes ``world.scenario`` but skips
        Event Log spam — used by HypothesisRunner which logs its own line.
        """
        if not prompt.strip():
            return {"error": "prompt is empty"}

        parse = self.parse(prompt)

        # 1) macro mutations (absolute logical-field targets).
        mutation_dicts = [m.model_dump() for m in parse.mutations]
        applied = self._kernel.apply_mutations(mutation_dicts)

        # 2) tariff / sanction actions (each also emits its own micro shock).
        tariff_applied: list[str] = []
        for act in parse.tariff_actions:
            res = await self._kernel.set_tariff(act.source, act.target, act.value)
            if res.get("ok"):
                tariff_applied.append(
                    f"{act.source}->{act.target} @ {act.value*100:.0f}%"
                )

        # 3) inject the scenario's Microstructural Volatility Vector into Quant.
        if parse.micro.is_active():
            await self._kernel.publish_micro_shock(parse.micro)

        await self._bus.publish(
            "world.scenario",
            prompt=prompt,
            category=parse.category.value,
            severity=parse.severity,
            subjects=parse.subjects,
            mutations=mutation_dicts,
            applied=applied,
            tariff_actions=[a.model_dump() for a in parse.tariff_actions],
            micro_impact=parse.micro.model_dump(),
        )

        if announce:
            from core.locale_prefs import dashboard_locale
            from ai.simulator_engine.log_i18n import format_scenario_log

            # 4) announce a rich, causal narrative on the unified event log.
            level = "danger" if parse.severity > 0.7 else "warn"
            locale = dashboard_locale()
            vi = locale.lower().startswith("vi")
            summary_bits: list[str] = []
            if applied:
                label = "vĩ mô" if vi else "macro"
                summary_bits.append(f"{label}[" + "; ".join(applied) + "]")
            if tariff_applied:
                label = "thương mại" if vi else "trade"
                summary_bits.append(f"{label}[" + "; ".join(tariff_applied) + "]")
            micro_label = "vi mô" if vi else "micro"
            summary_bits.append(
                f"{micro_label}[vol x{parse.micro.volatility_multiplier:.2f}, "
                f"OBI {parse.micro.order_flow_shock:+.2f}]"
            )
            message = format_scenario_log(
                prompt, parse.narrative, " ".join(summary_bits), locale
            )
            await self._bus.publish(
                "system.log", level=level, source="scenario", message=message
            )
            await self._bus.publish(
                "world.event",
                sim_day=self._kernel.world.sim_day,
                country=parse.subjects[0] if parse.subjects else "Global",
                message=parse.narrative,
                level=level,
            )

        return {
            "prompt": prompt,
            "category": parse.category.value,
            "severity": parse.severity,
            "subjects": parse.subjects,
            "mutations": mutation_dicts,
            "applied": applied,
            "tariff_actions": [a.model_dump() for a in parse.tariff_actions],
            "micro_impact": parse.micro.model_dump(),
            "narrative": parse.narrative,
        }
