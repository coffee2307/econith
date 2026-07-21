"""ECONITH :: ai.journalist.aggregator

World-term news wire — NOT a CPI tick paraphraser.

Real “thinking” in ECONITH lives in Tier-1 governors + material dialogue turns
(``world.dialogue.turn`` with ``source=llm``). The journalist’s job is to put
those grounded agent utterances on the wire.

It deliberately does **not** synthesize stories from oscillating ``world.macro``
CPI noise — that path produced fluent but empty LLM waffle that felt hardcoded.
Large execution fills may still get a short numeric line; silence beats filler.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from core.event_bus import Event, EventBus
from core.llm_pool import LLMKeyPool

logger = logging.getLogger("econith.ai.journalist")

__all__ = [
    "NumericDelta",
    "NewsLog",
    "LLMBackend",
    "TemplateLLMBackend",
    "OpenAICompatibleLLMBackend",
    "JournalistLLM",
]


# ---------------------------------------------------------------------------
# Structured facts
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class NumericDelta:
    """A single factual numeric state change harvested from the bus."""

    field: str
    value: float
    unit: str = ""
    entity: str = ""

    def render(self) -> str:
        if self.unit == "%":
            return f"{self.entity + ' ' if self.entity else ''}{self.field} {self.value:+.1f}%"
        if self.unit == "bps":
            return f"{self.entity + ' ' if self.entity else ''}{self.field} {self.value:+.0f}bps"
        prefix = f"{self.entity} " if self.entity else ""
        return f"{prefix}{self.field} = {self.value:.4g}"


@dataclass(slots=True)
class NewsLog:
    """A synthesized news line ready for the cockpit ticker."""

    ts: datetime
    category: str
    level: str
    message: str
    facts: list[NumericDelta] = field(default_factory=list)

    def format(self) -> str:
        stamp = self.ts.strftime("%Y-%m-%d %H:%M:%S")
        return f"[WORLD TERM - {stamp}] [{self.category.upper()}]: {self.message}"

    def to_cockpit(self, *, locale: str = "") -> dict[str, str]:
        out = {
            "ts": self.ts.isoformat(),
            "category": self.category,
            "level": self.level,
            "message": self.message,
        }
        if locale:
            out["locale"] = locale
        return out


# ---------------------------------------------------------------------------
# LLM backend seam
# ---------------------------------------------------------------------------
class LLMBackend(Protocol):
    async def complete(self, prompt: str, facts: list[NumericDelta]) -> str: ...


class TemplateLLMBackend:
    """Deterministic, dependency-free narrative synthesis.

    Produces institutional-desk-style prose from the structured facts. Replace
    with a real fast LLM by implementing :meth:`complete`.
    """

    async def complete(self, prompt: str, facts: list[NumericDelta]) -> str:
        if not facts:
            return ""
        # Deterministic one-liner from netted facts only — no invented China/Core waffle.
        drivers = "; ".join(f.render() for f in facts[:5])
        return f"Material moves: {drivers}."


class OpenAICompatibleLLMBackend:
    """OpenAI-compatible backend (Groq, OpenAI, etc.) with multi-key failover."""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "",
        model: str = "",
        timeout_s: float = 20.0,
        key_pool: LLMKeyPool | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._timeout_s = timeout_s
        if key_pool is not None:
            self._pool = key_pool
        else:
            from core.llm_pool import parse_llm_api_keys

            self._pool = LLMKeyPool(parse_llm_api_keys(api_key))

    async def complete(self, prompt: str, facts: list[NumericDelta]) -> str:
        def _call() -> str:
            response = self._pool.create_chat_completion(
                base_url=self._base_url,
                model=self._model,
                timeout=self._timeout_s,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are ECONITH's world-event news writer. "
                            "Write one short factual sentence or two. "
                            "Never invent volatility stories from canceling +/- moves. "
                            "If facts are empty or noise, reply with exactly: SKIP"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                max_tokens=120,
            )
            content = response.choices[0].message.content or ""
            return content.strip()

        text = await asyncio.to_thread(_call)
        if not text or text.strip().upper() == "SKIP":
            return ""
        return text


# ---------------------------------------------------------------------------
# The Journalist consumer
# ---------------------------------------------------------------------------
class JournalistLLM:
    """Async EventBus consumer that emits synthesized breaking-news logs."""

    def __init__(
        self,
        bus: EventBus,
        backend: LLMBackend | None = None,
        *,
        flush_interval_s: float = 30.0,
        history: int = 100,
        min_delta: float = 0.5,
    ) -> None:
        self._bus = bus
        self._backend = backend or TemplateLLMBackend()
        self._flush_interval = flush_interval_s
        self._min_delta = min_delta
        self._pending: list[NumericDelta] = []
        self._baseline: dict[str, float] = {}
        self._last_digest = ""
        self._last_message = ""
        self._category = "MACRO"
        self._level = "info"
        self._logs: deque[NewsLog] = deque(maxlen=history)
        self._fact_seen: dict[str, float] = {}
        self._fact_cooldown_s: float = 120.0
        self._task: asyncio.Task[None] | None = None
        self._running = False
        # CPI must move by this many percentage points (net) to make news.
        self._cpi_material_pp = 1.5

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        # No world.macro CPI subscription — tick noise → LLM waffle.
        self._bus.subscribe("world.micro_impact", self._on_micro_impact)
        self._bus.subscribe("world.dialogue.turn", self._on_dialogue_turn)
        self._bus.subscribe("world.agent.narrative", self._on_agent_narrative)
        self._bus.subscribe("quant.fill", self._on_fill)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._flush_loop(), name="journalist-llm")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- ingest ---------------------------------------------------------------
    def _enqueue_if_changed(
        self,
        key: str,
        value: float,
        *,
        field: str,
        unit: str = "",
        entity: str = "",
        threshold: float | None = None,
    ) -> None:
        """Seed baseline silently; enqueue only when the value moves materially."""
        prev = self._baseline.get(key)
        self._baseline[key] = value
        if prev is None:
            return
        tol = self._min_delta if threshold is None else threshold
        if abs(value - prev) < tol:
            return
        delta_val = value - prev if unit in ("%", "bps") else value
        self._pending.append(
            NumericDelta(field=field, value=delta_val, unit=unit, entity=entity)
        )

    async def _on_dialogue_turn(self, event: Event) -> None:
        """Wire agent dialogue — LLM turns and honest status fallbacks."""
        source = str(event.payload.get("source") or "")
        if source not in ("llm", "status"):
            return
        utterances = event.payload.get("utterances") or []
        if not utterances:
            return
        lines: list[str] = []
        for u in utterances[:3]:
            if not isinstance(u, dict):
                continue
            text = str(u.get("text") or "").strip()
            if not text or len(text) < 16:
                continue
            low = text.lower()
            if "selecting action" in low or "chọn hành động" in low:
                continue
            if self._looks_like_cpi_waffle(text):
                continue
            role = str(u.get("role") or "").strip()
            country = str(u.get("country") or "").strip()
            prefix = f"{role} ({country}): " if role and country else ""
            lines.append(f"{prefix}{text}" if prefix else text)
        if not lines:
            return
        reason = str(event.payload.get("material_reason") or "").strip()
        message = " | ".join(lines)
        if reason:
            message = f"[{reason}] {message}"
        if source == "status":
            message = f"[status] {message}"
        await self._publish_wire(
            message,
            category="MACRO",
            level="info" if source == "status" else str(event.payload.get("level") or "warn"),
            dedupe_key=f"dlg:{source}:{message[:96]}",
        )

    async def _on_agent_narrative(self, event: Event) -> None:
        """Promote grounded agent narratives that came from dialogue, not templates."""
        if str(event.payload.get("provenance") or event.payload.get("source") or "") not in (
            "llm",
            "dialogue",
        ):
            return
        if str(event.payload.get("provenance") or "") == "control_law":
            return
        text = str(event.payload.get("text") or "").strip()
        if not text or len(text) < 20 or self._looks_like_cpi_waffle(text):
            return
        # Dialogue path already published the turn; skip duplicate agent echoes.
        if str(event.payload.get("source") or "") == "dialogue":
            return
        await self._publish_wire(
            text,
            category="MACRO",
            level=str(event.payload.get("level") or "info"),
            dedupe_key=f"nar:{text[:96]}",
        )

    async def _on_micro_impact(self, event: Event) -> None:
        # Coupling vector only — never republish canned ``fact`` strings.
        return

    async def _publish_wire(
        self,
        message: str,
        *,
        category: str,
        level: str,
        dedupe_key: str,
    ) -> None:
        now = asyncio.get_event_loop().time()
        if now - self._fact_seen.get(dedupe_key, 0.0) < self._fact_cooldown_s:
            return
        self._fact_seen[dedupe_key] = now
        if len(self._fact_seen) > 500:
            cutoff = now - self._fact_cooldown_s * 2
            self._fact_seen = {k: v for k, v in self._fact_seen.items() if v > cutoff}
        from core.locale_prefs import dashboard_locale

        locale = dashboard_locale()
        log = NewsLog(
            ts=datetime.now(timezone.utc),
            category=category,
            level=level,
            message=message,
        )
        self._logs.appendleft(log)
        await self._bus.publish("journalist.news", **log.to_cockpit(locale=locale))
        logger.info(log.format())

    @staticmethod
    def _looks_like_cpi_waffle(text: str) -> bool:
        """Reject fluent-but-empty CPI oscillation paragraphs."""
        t = text.lower()
        markers = (
            "dao động",
            "tăng giảm",
            "giảm phát",
            "oscillat",
            "đã giảm",
            "rồi giảm",
            "then fell",
            "up and down",
            "biến động nhỏ",
            "ổn định tương đối",
        )
        if "cpi" in t or "chỉ số giá" in t or "consumer price" in t:
            if any(m in t for m in markers):
                return True
            # Same-magnitude up/down storytelling.
            if ("tăng" in t and "giảm" in t) or ("rose" in t and "fell" in t):
                return True
        return False

    async def _on_fill(self, event: Event) -> None:
        vol = float(event.payload.get("filledVolume", 0.0))
        price = float(event.payload.get("fillPrice", 0.0))
        notional = vol * price
        if notional <= 250_000.0:
            return
        asset = str(event.payload.get("asset", "") or "asset")
        await self._publish_wire(
            f"Block execution: {asset} ~${notional:,.0f}",
            category="EXECUTION",
            level="warn",
            dedupe_key=f"fill:{asset}:{int(notional)}",
        )

    # -- synthesis loop (legacy numeric path — fills only; CPI disabled) ------
    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._synthesize()

    async def _synthesize(self) -> None:
        """Numeric flush is intentionally quiet after CPI wire was removed."""
        if not self._pending:
            return
        raw = list(self._pending)
        self._pending.clear()
        facts = self._net_material_facts(raw)
        if not facts:
            return
        # Never LLM-paraphrase remaining numeric crumbs into macro essays.
        drivers = "; ".join(f.render() for f in facts[:4])
        if self._looks_like_cpi_waffle(drivers):
            return
        await self._publish_wire(
            f"Material moves: {drivers}",
            category=self._category,
            level=self._level,
            dedupe_key=f"num:{drivers[:96]}",
        )

    def _net_material_facts(self, facts: list[NumericDelta]) -> list[NumericDelta]:
        """Collapse same entity+field (cancel +x then -x) and drop noise."""
        nets: dict[tuple[str, str, str], float] = {}
        gross: dict[tuple[str, str, str], float] = {}
        for f in facts:
            key = (f.entity, f.field, f.unit)
            nets[key] = nets.get(key, 0.0) + float(f.value)
            gross[key] = gross.get(key, 0.0) + abs(float(f.value))
        out: list[NumericDelta] = []
        for (entity, field_name, unit), net in nets.items():
            g = gross[(entity, field_name, unit)]
            # Oscillation spam: large back-and-forth, tiny net direction.
            if g > 1e-9 and abs(net) < 0.40 * g:
                continue
            if field_name == "CPI":
                if abs(net) < self._cpi_material_pp:
                    continue
            elif unit == "%":
                if abs(net) < max(self._min_delta, 1.0):
                    continue
            elif abs(net) < self._min_delta:
                continue
            out.append(NumericDelta(field=field_name, value=net, unit=unit, entity=entity))
        out.sort(key=lambda d: abs(d.value), reverse=True)
        return out

    @staticmethod
    def _build_prompt(facts: list[NumericDelta], *, locale: str = "en") -> str:
        """Structural prompt: factual numeric deltas -> natural news request."""
        lang = "Vietnamese" if locale.startswith("vi") else "English"
        lines = "\n".join(f"- {f.render()}" for f in facts)
        return (
            f"You are ECONITH's objective global financial news terminal. "
            f"Write in {lang}. Translate ONLY these netted material deltas into "
            f"one or two short sentences. Do not mention countries that are not "
            f"listed. Do not invent oscillating volatility (+x then -x). "
            f"If nothing is material, reply SKIP.\n"
            f"Netted state deltas:\n{lines}"
        )

    # -- reads ----------------------------------------------------------------
    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        return [log.to_cockpit() for log in list(self._logs)[:limit]]
