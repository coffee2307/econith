"""ECONITH :: core.telemetry

A single read-model for the dashboard. The MetricsHub subscribes to the
EventBus and keeps the latest consolidated snapshot of:

  * simulated time (from the TimeEngine)
  * market microstructure (price, OBI, Volume Delta)
  * alternative data (funding rate, open interest, liquidations)
  * AI ensemble decision (direction / action / regime / attribution)
  * ECONITH World macro state (GDP, inflation, rate, tax, ...)
  * Sentinel governance status
  * a rolling buffer of Quant ops logs + World research headlines (separate feeds)

The FastAPI ``/api/v1/stream/metrics`` WebSocket simply serialises
``MetricsHub.snapshot()`` on a fixed cadence -- the hub is the only place that
has to understand the event topics.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any

from core.engine import TimeEngine
from core.event_bus import Event, EventBus
from core.mode import get_mode_manager

MAX_EVENTS = 60
MAX_WORLD_EVENTS = 60
MAX_WORLD_AGENTS = 60
# Wall-clock throttle per actor:country. Story-level repetition is suppressed
# at the source (WorldKernel narrative gate), so this only smooths bursts.
AGENT_FEED_COOLDOWN_S = 20.0

# Sources routed to the World research feed (never Quant's execution log).
_WORLD_SOURCES = frozenset({
    "world",
    "corporate",
    "government",
    "society",
    "sovereign",
    "journalist",
    "scenario",
    "hypothesis",
    "regime",
})

# Always surface these sources on the World event feed (even at info level).
_WORLD_INFO_SOURCES = frozenset({"hypothesis", "scenario", "status"})
# Sources that may emit info-level lines into the Quant ops log.
_QUANT_INFO_SOURCES = frozenset({
    "sentinel",
    "ai",
    "streamer",
    "exchange_bridge",
    "exchange",
    "execution",
    "quant",
    "ccxt",
    "routing",
    "system",
})


class MetricsHub:
    def __init__(self, bus: EventBus, time_engine: TimeEngine) -> None:
        self._bus = bus
        self._time = time_engine
        self._market: dict[str, Any] = {
            "symbol": None,
            "price": None,
            "mid": None,
            "best_bid": None,
            "best_ask": None,
            "obi": None,
            "bid_volume": None,
            "ask_volume": None,
            "volume_delta": None,
            "buy_volume": None,
            "sell_volume": None,
            "trade_count": None,
        }
        self._alt: dict[str, Any] = {
            "funding_rate": None,
            "time_to_funding_s": None,
            "open_interest": None,
            "oi_change_pct": None,
            "liquidation_notional": None,
        }
        self._ai: dict[str, Any] = {}
        self._routing: dict[str, Any] = {}
        self._debate: dict[str, Any] = {}
        self._alpha: dict[str, Any] = {}
        self._world: dict[str, Any] = {}
        self._sentinel: dict[str, Any] = {}
        self._quant_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._world_events: deque[dict[str, Any]] = deque(maxlen=MAX_WORLD_EVENTS)
        self._world_agents: deque[dict[str, Any]] = deque(maxlen=MAX_WORLD_AGENTS)
        self._agent_last_ts: dict[str, float] = {}
        self._headline_last_ts: dict[str, float] = {}
        self._headline_last: str = ""
        self._journalist_last_ts: float = 0.0
        self._journalist_last_msg: str = ""
        self._dialogue_turns: deque[dict[str, Any]] = deque(maxlen=40)
        self._execution: dict[str, Any] = {}

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("indicator.obi", self._on_obi)
        self._bus.subscribe("indicator.volume_delta", self._on_volume_delta)
        self._bus.subscribe("alt.funding_rate", self._on_funding)
        self._bus.subscribe("alt.open_interest", self._on_open_interest)
        self._bus.subscribe("alt.liquidation", self._on_liquidation)
        self._bus.subscribe("ai.signal", self._on_ai_signal)
        self._bus.subscribe("quant.route.plan", self._on_route_plan)
        self._bus.subscribe("meta.debate.verdict", self._on_debate)
        self._bus.subscribe("ai.alpha.candidate", self._on_alpha)
        self._bus.subscribe("world.macro", self._on_world_macro)
        self._bus.subscribe("sentinel.status", self._on_sentinel_status)
        self._bus.subscribe("sentinel.emergency", self._on_emergency)
        self._bus.subscribe("system.log", self._on_log)
        self._bus.subscribe("journalist.news", self._on_journalist_news)
        self._bus.subscribe("quant.fill", self._on_quant_fill)
        self._bus.subscribe("order.update", self._on_order_update)
        self._bus.subscribe("world.agent.narrative", self._on_world_agent)
        self._bus.subscribe("world.headline", self._on_world_headline)
        self._bus.subscribe("world.dialogue.turn", self._on_dialogue_turn)
        self._bus.subscribe("execution.status", self._on_execution_status)

    # -- handlers -------------------------------------------------------------
    async def _on_ticker(self, event: Event) -> None:
        self._market["symbol"] = event.payload.get("symbol")
        self._market["price"] = round(float(event.payload["price"]), 2)

    async def _on_obi(self, event: Event) -> None:
        p = event.payload
        self._market.update(
            obi=round(float(p["obi"]), 4),
            bid_volume=round(float(p["bid_volume"]), 4),
            ask_volume=round(float(p["ask_volume"]), 4),
            mid=round(float(p["mid"]), 2) if p.get("mid") is not None else None,
            best_bid=p.get("best_bid"),
            best_ask=p.get("best_ask"),
        )

    async def _on_volume_delta(self, event: Event) -> None:
        p = event.payload
        self._market.update(
            volume_delta=round(float(p["volume_delta"]), 4),
            buy_volume=round(float(p["buy_volume"]), 4),
            sell_volume=round(float(p["sell_volume"]), 4),
            trade_count=int(p["trade_count"]),
        )

    async def _on_funding(self, event: Event) -> None:
        p = event.payload
        self._alt["funding_rate"] = p.get("funding_rate")
        self._alt["time_to_funding_s"] = p.get("time_to_funding_s")

    async def _on_open_interest(self, event: Event) -> None:
        p = event.payload
        self._alt["open_interest"] = p.get("open_interest")
        self._alt["oi_change_pct"] = p.get("oi_change_pct")

    async def _on_liquidation(self, event: Event) -> None:
        self._alt["liquidation_notional"] = event.payload.get("total_notional")

    async def _on_ai_signal(self, event: Event) -> None:
        p = event.payload
        self._ai = {
            "action": p.get("action"),
            "direction": p.get("direction"),
            "confidence": p.get("confidence"),
            "regime": p.get("regime"),
            "regime_confidence": p.get("regime_confidence"),
            "weights": p.get("weights"),
            "per_agent": p.get("per_agent"),
            "explain": p.get("explain"),
            "agent_brain": p.get("agent_brain"),
            "regime_brain": p.get("regime_brain"),
        }

    async def _on_route_plan(self, event: Event) -> None:
        self._routing = dict(event.payload)

    async def _on_debate(self, event: Event) -> None:
        self._debate = dict(event.payload)

    async def _on_alpha(self, event: Event) -> None:
        self._alpha = dict(event.payload)

    async def _on_world_macro(self, event: Event) -> None:
        self._world = {
            "sim_day": event.payload.get("sim_day"),
            "global": event.payload.get("global"),
            "countries": event.payload.get("countries"),
            "tariffs": event.payload.get("tariffs"),
            "alliances": event.payload.get("alliances"),
            # Tier-3 population read-model + Tier-1/2 hierarchy telemetry so the
            # dashboard can render the cognitive world, not just the macro grid.
            "agent_population": event.payload.get("agent_population"),
            "hierarchy_telemetry": event.payload.get("hierarchy_telemetry"),
            "governor_llm": event.payload.get("governor_llm"),
            "dialogue": event.payload.get("dialogue"),
            # bidirectional feedback-loop telemetry (macro<->micro coupling)
            "micro_impact": event.payload.get("micro_impact"),
            "market": event.payload.get("market"),
        }

    async def _on_sentinel_status(self, event: Event) -> None:
        self._sentinel = dict(event.payload)

    async def _on_execution_status(self, event: Event) -> None:
        self._execution = dict(event.payload)
        routing = str(event.payload.get("execution_routing") or "")
        if routing == "DEGRADED":
            detail = str(event.payload.get("detail") or "exchange unreachable")
            self._push_quant_event(
                level="warn",
                source="ccxt",
                message=f"Execution DEGRADED — {detail}",
                ts=event.ts,
            )

    async def _on_emergency(self, event: Event) -> None:
        self._push_quant_event(
            level="danger",
            source="sentinel",
            message=f"EMERGENCY [{event.payload.get('action')}] {event.payload.get('reason')}",
            ts=event.ts,
        )

    async def _on_log(self, event: Event) -> None:
        level = event.payload.get("level", "info")
        source = event.payload.get("source", "system")
        message = event.payload.get("message", "")

        if source in _WORLD_SOURCES:
            if level in ("warn", "danger") or source in _WORLD_INFO_SOURCES:
                self._push_world_event(
                    level=level, source=source, message=message, ts=event.ts
                )
            return

        if level in ("danger", "warn"):
            self._push_quant_event(
                level=level, source=source, message=message, ts=event.ts
            )
            return
        if source in _QUANT_INFO_SOURCES:
            self._push_quant_event(
                level=level, source=source, message=message, ts=event.ts
            )

    async def _on_quant_fill(self, event: Event) -> None:
        symbol = event.payload.get("asset") or event.payload.get("symbol") or "—"
        status = str(event.payload.get("status") or "FILLED").upper()
        vol = float(event.payload.get("filledVolume") or event.payload.get("quantity") or 0)
        price = float(event.payload.get("fillPrice") or event.payload.get("price") or 0)
        if status != "FILLED" or vol <= 0:
            self._push_quant_event(
                level="warn",
                source="execution",
                message=f"Order {status} {symbol} — {event.payload.get('detail') or 'no fill'}",
                ts=event.ts,
            )
            return
        notional = vol * price
        level = "warn" if notional >= 250_000 else "ok"
        self._push_quant_event(
            level=level,
            source="execution",
            message=f"Fill {symbol} qty={vol:.4f} @ {price:.2f} (notional ${notional:,.0f})",
            ts=event.ts,
        )

    async def _on_order_update(self, event: Event) -> None:
        status = str(event.payload.get("status", "")).upper()
        if status not in ("SUBMITTED", "FILLED", "REJECTED", "CANCELLED"):
            return
        symbol = event.payload.get("symbol", "—")
        side = event.payload.get("side", "—")
        algo = event.payload.get("algo", "")
        level = "warn" if status == "REJECTED" else "info"
        suffix = f" via {algo}" if algo else ""
        self._push_quant_event(
            level=level,
            source="routing",
            message=f"Order {status}: {side} {symbol}{suffix}",
            ts=event.ts,
        )

    async def _on_world_agent(self, event: Event) -> None:
        import asyncio

        text = str(event.payload.get("text", ""))
        actor = str(event.payload.get("actor", ""))
        level = str(event.payload.get("level", "info"))
        now = asyncio.get_event_loop().time()
        country = str(event.payload.get("country", ""))
        # Throttle each semantic speaker/country stream. Danger events used to
        # bypass cooldown entirely, flooding the UI every simulation tick with
        # the same corporate-flight sentence whose amount changed slightly.
        feed_key = f"{actor}:{country}"
        last = self._agent_last_ts.get(feed_key, 0.0)
        cooldown = AGENT_FEED_COOLDOWN_S  # wall-clock: readable even at 20x sim speed
        if now - last < cooldown:
            return
        if self._world_agents:
            prev = self._world_agents[0]
            if prev.get("text") == text and prev.get("actor") == actor:
                return
            if (
                prev.get("actor") == actor
                and prev.get("country") == event.payload.get("country")
                and text[:40] == str(prev.get("text", ""))[:40]
            ):
                return
        self._agent_last_ts[feed_key] = now
        self._world_agents.appendleft(
            {
                "ts": event.ts.isoformat(),
                "sim_day": event.payload.get("sim_day"),
                "actor": event.payload.get("actor", ""),
                "country": event.payload.get("country", ""),
                "text": text,
                "level": level,
                "source": event.payload.get("source", ""),
                "locale": event.payload.get("locale", "en"),
            }
        )

    async def _on_world_headline(self, event: Event) -> None:
        import asyncio

        message = str(event.payload.get("message", ""))
        if not message or message == self._headline_last:
            return
        source = str(event.payload.get("source", "world"))
        country = str(event.payload.get("country", ""))
        now = asyncio.get_event_loop().time()
        headline_key = f"{source}:{country}"
        # Event headlines describe the same underlying state transition as the
        # agent feed. Keep one material headline per source/country window.
        if now - self._headline_last_ts.get(headline_key, 0.0) < AGENT_FEED_COOLDOWN_S:
            return
        self._headline_last_ts[headline_key] = now
        self._headline_last = message
        self._push_world_event(
            level=event.payload.get("level", "info"),
            source=source,
            message=message,
            ts=event.ts,
        )

    async def _on_journalist_news(self, event: Event) -> None:
        """Admit all non-empty Journalist levels (info/ok/warn/danger).

        Previously only warn/danger reached the dashboard, so the terminal's
        ordinary MACRO digests never appeared in the Event Log.
        """
        import asyncio

        message = str(event.payload.get("message", "")).strip()
        if not message:
            return
        now = asyncio.get_event_loop().time()
        # Throttle near-identical digests; keep the feed readable at high speed.
        if (
            message == self._journalist_last_msg
            and now - self._journalist_last_ts < AGENT_FEED_COOLDOWN_S
        ):
            return
        if now - self._journalist_last_ts < 8.0 and message[:80] == self._journalist_last_msg[:80]:
            return
        self._journalist_last_ts = now
        self._journalist_last_msg = message
        level = str(event.payload.get("level", "info") or "info")
        self._world_events.appendleft(
            {
                "ts": event.ts.isoformat(),
                "level": level,
                "source": "journalist",
                "message": message,
                "category": event.payload.get("category", ""),
                "locale": event.payload.get("locale", ""),
            }
        )

    async def _on_dialogue_turn(self, event: Event) -> None:
        payload = dict(event.payload)
        self._dialogue_turns.appendleft(payload)
        # Surface the lead utterance into the agent feed so the UI chat updates
        # even before a dedicated dialogue panel is rendered.
        utterances = payload.get("utterances") or []
        if not utterances:
            return
        lead = utterances[0] if isinstance(utterances[0], dict) else {}
        text = str(lead.get("text", "")).strip()
        if not text:
            return
        self._world_agents.appendleft(
            {
                "ts": event.ts.isoformat(),
                "sim_day": payload.get("tick"),
                "actor": lead.get("role") or lead.get("agent_id") or "Dialogue",
                "country": lead.get("country", ""),
                "text": text,
                "level": payload.get("level", "info"),
                "source": "dialogue",
                "locale": lead.get("locale", "en"),
                "metrics": lead.get("metrics") or [],
                "provenance": payload.get("source", "dialogue"),
            }
        )

    def _push_quant_event(
        self, level: str, source: str, message: str, ts: datetime
    ) -> None:
        self._quant_events.appendleft(
            {
                "ts": ts.isoformat(),
                "level": level,
                "source": source,
                "message": message,
            }
        )

    def _push_world_event(
        self, level: str, source: str, message: str, ts: datetime
    ) -> None:
        self._world_events.appendleft(
            {
                "ts": ts.isoformat(),
                "level": level,
                "source": source,
                "message": message,
            }
        )

    # -- read model -----------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "time": {
                "sim_day": self._time.sim_day,
                "multiplier": self._time.multiplier,
                "running": self._time.running,
            },
            "market": dict(self._market),
            "alt": dict(self._alt),
            "ai": dict(self._ai),
            "routing": dict(self._routing),
            "debate": dict(self._debate),
            "alpha": dict(self._alpha),
            "world": dict(self._world),
            "sentinel": dict(self._sentinel),
            "events": list(self._quant_events),
            "world_events": list(self._world_events),
            "world_agents": list(self._world_agents),
            "world_dialogue": list(self._dialogue_turns),
            "quant_mode": get_mode_manager().snapshot(),
            "execution": dict(self._execution) if self._execution else None,
        }
