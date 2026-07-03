"""ECONITH :: ai.journalist.aggregator

The Journalist LLM -- semantic narrative synthesis engine.

An objective global financial news terminal. It subscribes to the internal
EventBus macro/micro channels (``world.macro``, ``world.micro_impact``,
``core.macro.context``, ``quant.fill``), buffers the raw numeric multi-agent
state deltas, and translates them into cohesive, contextualized breaking-news
logs.

Rather than hardcoded rules, it builds a structured prompt from the factual
numeric deltas and hands it to a pluggable :class:`LLMBackend`. A deterministic
template backend ships by default so the terminal is fully operable without any
external model; swapping in a real LLM means implementing one ``complete``
method.

Required log format:
    [WORLD TERM - TIMESTAMP] [CATEGORY]: <cohesive narrative synthesis>.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from core.event_bus import Event, EventBus

logger = logging.getLogger("econith.ai.journalist")

__all__ = [
    "NumericDelta",
    "NewsLog",
    "LLMBackend",
    "TemplateLLMBackend",
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

    def to_cockpit(self) -> dict[str, str]:
        return {
            "ts": self.ts.isoformat(),
            "category": self.category,
            "level": self.level,
            "message": self.message,
        }


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
            return "Markets steady; no material structural re-pricing this interval."
        drivers = ", ".join(f.render() for f in facts[:4])
        lead = facts[0]
        if abs(lead.value) > (5.0 if lead.unit == "%" else 1.0):
            tone = "Geopolitical friction triggers localized structural re-routing."
        else:
            tone = "Incremental macro drift observed across the global matrix."
        return (
            f"{tone} Chinese and regional corporate infrastructure recalibrate "
            f"supply lines while the Core reprices systemic risk. Drivers: {drivers}."
        )


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
        min_delta: float = 0.05,
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
        self._task: asyncio.Task[None] | None = None
        self._running = False

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        self._bus.subscribe("world.macro", self._on_world_macro)
        self._bus.subscribe("world.sovereign", self._on_world_macro)
        self._bus.subscribe("world.micro_impact", self._on_micro_impact)
        self._bus.subscribe("core.macro.context", self._on_core_macro)
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

    async def _on_world_macro(self, event: Event) -> None:
        self._category = "MACRO"
        countries = event.payload.get("countries", {}) or {}
        for code, snap in list(countries.items())[:6]:
            inflation = snap.get("inflation")
            if inflation is not None:
                self._enqueue_if_changed(
                    f"{code}:cpi",
                    float(inflation) * 100.0,
                    field="CPI",
                    unit="%",
                    entity=code,
                    threshold=0.1,
                )
            export = snap.get("export_index")
            if export is not None:
                self._enqueue_if_changed(
                    f"{code}:export",
                    float(export) - 100.0,
                    field="Export Index",
                    unit="%",
                    entity=code,
                    threshold=1.0,
                )

    async def _on_micro_impact(self, event: Event) -> None:
        self._category = "GEOPOLITICS"
        self._level = "warn"
        fact = event.payload.get("fact")
        if not fact:
            return
        log = NewsLog(
            ts=datetime.now(timezone.utc),
            category=self._category,
            level=self._level,
            message=str(fact),
        )
        self._logs.appendleft(log)
        await self._bus.publish("journalist.news", **log.to_cockpit())
        logger.info(log.format())
        self._level = "info"

    async def _on_core_macro(self, event: Event) -> None:
        macro = event.payload.get("macro", {}) or {}
        btc_prem = macro.get("btc_risk_premium")
        if btc_prem is not None:
            self._enqueue_if_changed(
                "core:btc_prem",
                float(btc_prem) * 100.0,
                field="BTC systemic risk premium",
                unit="%",
                entity="Core",
                threshold=0.5,
            )

    async def _on_fill(self, event: Event) -> None:
        vol = float(event.payload.get("filledVolume", 0.0))
        price = float(event.payload.get("fillPrice", 0.0))
        notional = vol * price
        if notional <= 250_000.0:
            return
        self._category = "EXECUTION"
        self._level = "warn"
        self._pending.append(
            NumericDelta(
                field="block execution",
                value=notional,
                unit="",
                entity=str(event.payload.get("asset", "")),
            )
        )

    # -- synthesis loop -------------------------------------------------------
    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._synthesize()

    async def _synthesize(self) -> None:
        if not self._pending:
            return
        facts = list(self._pending)
        self._pending.clear()

        digest = "|".join(f"{f.entity}:{f.field}:{f.value:.4g}" for f in facts)
        if digest == self._last_digest:
            return
        self._last_digest = digest

        prompt = self._build_prompt(facts)
        try:
            message = await self._backend.complete(prompt, facts)
        except Exception:  # noqa: BLE001 - a model fault must not kill the feed
            logger.exception("journalist backend failed")
            return

        if message == self._last_message:
            return
        self._last_message = message

        log = NewsLog(
            ts=datetime.now(timezone.utc),
            category=self._category,
            level=self._level,
            message=message,
            facts=facts,
        )
        self._logs.appendleft(log)
        self._level = "info"
        await self._bus.publish("journalist.news", **log.to_cockpit())
        if log.level in ("warn", "danger"):
            logger.info(log.format())
        else:
            logger.debug(log.format())

    @staticmethod
    def _build_prompt(facts: list[NumericDelta]) -> str:
        """Structural prompt: factual numeric deltas -> natural news request."""
        lines = "\n".join(f"- {f.render()}" for f in facts)
        return (
            "You are ECONITH's objective global financial news terminal. "
            "Translate the following raw numeric multi-agent state deltas into a "
            "single cohesive, contextualized breaking-news paragraph. Be factual, "
            "avoid hype, and explain the causal chain.\n"
            f"State deltas:\n{lines}"
        )

    # -- reads ----------------------------------------------------------------
    def recent(self, limit: int = 20) -> list[dict[str, str]]:
        return [log.to_cockpit() for log in list(self._logs)[:limit]]
