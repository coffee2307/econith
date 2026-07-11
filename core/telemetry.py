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
MAX_WORLD_EVENTS = 40
MAX_WORLD_AGENTS = 50
AGENT_FEED_COOLDOWN_S = 50.0

# Sources routed to the World research feed (never Quant's execution log).
_WORLD_SOURCES = frozenset({
    "world",
    "corporate",
    "government",
    "society",
    "sovereign",
    "journalist",
    "scenario",
    "regime",
})

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
        self._headline_last: str = ""

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
            # bidirectional feedback-loop telemetry (macro<->micro coupling)
            "micro_impact": event.payload.get("micro_impact"),
            "market": event.payload.get("market"),
        }

    async def _on_sentinel_status(self, event: Event) -> None:
        self._sentinel = dict(event.payload)

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
            if level in ("warn", "danger"):
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
        vol = float(event.payload.get("filledVolume") or event.payload.get("quantity") or 0)
        price = float(event.payload.get("fillPrice") or event.payload.get("price") or 0)
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
        last = self._agent_last_ts.get(actor, 0.0)
        cooldown = AGENT_FEED_COOLDOWN_S  # wall-clock: readable even at 20x sim speed
        if level != "danger" and now - last < cooldown:
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
        self._agent_last_ts[actor] = now
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
        message = str(event.payload.get("message", ""))
        if not message or message == self._headline_last:
            return
        self._headline_last = message
        self._push_world_event(
            level=event.payload.get("level", "info"),
            source=event.payload.get("source", "world"),
            message=message,
            ts=event.ts,
        )

    async def _on_journalist_news(self, event: Event) -> None:
        level = event.payload.get("level", "info")
        if level not in ("warn", "danger"):
            return
        self._push_world_event(
            level=level,
            source="journalist",
            message=event.payload.get("message", ""),
            ts=event.ts,
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
            "quant_mode": get_mode_manager().snapshot(),
        }
