"""ECONITH :: infrastructure.websocket.streamer

Binance market-data streamer (master plan, Phase 1, Step 1).

If real Binance credentials are absent (the default in local/dev), the streamer
transparently becomes a **high-fidelity mock generator** that emits JSON frames
which match Binance's *production* schemas exactly:

  * ``<symbol>@aggTrade``        -- tick-by-tick aggregate trades
  * ``<symbol>@depth20@100ms``   -- L2 partial order book, top-20 levels

Generation cadence is governed by the Phase 0 ``TimeEngine`` contract: the base
order-book refresh is 100ms at 1x and scales down with the time multiplier
(``100ms / multiplier``), so 20x time == 20x faster market data. When the clock
is paused the stream idles.

Raw frames are pushed onto an async buffer and published to the central
``EventBus`` on topics ``md.aggTrade`` and ``md.depth``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from config.environment import get_environment
from core.engine import TimeEngine
from core.event_bus import EventBus

logger = logging.getLogger("econith.infra.ws.streamer")

# Base order-book cadence at 1x (Binance @depth20@100ms == 100ms).
BASE_DEPTH_INTERVAL_S = 0.10
DEPTH_LEVELS = 20


@dataclass
class MockMarketState:
    """Mutable state for the synthetic price/orderbook process."""

    symbol: str = "BTCUSDT"
    session_open: float = 60_000.0
    mid: float = 60_000.0
    tick_size: float = 0.10
    base_qty: float = 0.85          # nominal liquidity per level
    drift: float = 0.0              # per-step drift (random walk is ~driftless)
    vol: float = 0.00035            # per-step log-return std (low, stable demo)
    momentum: float = 0.0           # smoothed recent return -> drives book skew
    agg_id: int = 0
    update_id: int = 0
    # transient anomaly injection
    pending_shock_pct: float = 0.0
    stale_frames: int = 0           # number of upcoming frames to back-date
    stale_lag_ms: int = 1_500
    vol_multiplier: float = 1.0     # decays back to 1.0 after a shock
    last_update_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class BinanceWebSocketStreamer:
    """Streams Binance market data (live or high-fidelity mock)."""

    def __init__(
        self,
        bus: EventBus,
        time_engine: TimeEngine,
        symbol: str = "BTCUSDT",
        force_mock: bool | None = None,
    ) -> None:
        self._env = get_environment()
        self._bus = bus
        self._time = time_engine
        self._state = MockMarketState(symbol=symbol.upper(), session_open=60_000.0)
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10_000)
        self._running = False
        self._task: asyncio.Task[None] | None = None

        if force_mock is None:
            self._mock = not self._env.has_binance_data_credentials
        else:
            self._mock = force_mock

    # -- lifecycle ------------------------------------------------------------
    @property
    def symbol(self) -> str:
        return self._state.symbol

    @property
    def is_mock(self) -> bool:
        return self._mock

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        mode = "MOCK" if self._mock else "LIVE"
        await self._bus.publish(
            "system.log",
            level="info",
            source="streamer",
            message=f"market data streamer online [{mode}] {self.symbol}",
        )
        self._task = asyncio.create_task(self.run(), name="md-streamer")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- anomaly injection (drives Sentinel demos) ----------------------------
    def inject_anomaly(self, kind: Literal["shock", "latency", "vol"] = "shock") -> None:
        """Queue a synthetic market anomaly for the next generation cycle."""
        if kind == "shock":
            self._state.pending_shock_pct = -0.15      # instant -15% flash crash
            self._state.vol_multiplier = 6.0
        elif kind == "latency":
            self._state.stale_frames = 6               # back-date next 6 frames
        elif kind == "vol":
            self._state.vol_multiplier = 8.0
        logger.warning("anomaly injected: %s", kind)

    # -- main loop ------------------------------------------------------------
    async def run(self) -> None:
        """Generate (mock) or relay (live) market data until stopped."""
        if not self._mock:
            await self._run_live()
            return

        while self._running:
            # Idle while the simulated clock is paused.
            if not self._time.running:
                await asyncio.sleep(0.05)
                continue

            multiplier = max(1, self._time.multiplier)
            interval = BASE_DEPTH_INTERVAL_S / multiplier

            # 1) advance the synthetic mid price
            self._advance_price()

            # 2) emit a burst of aggregate trades for this cycle
            for frame in self._gen_agg_trades():
                await self._emit("md.aggTrade", frame)

            # 3) emit one depth20 snapshot
            depth = self._gen_depth()
            await self._emit("md.depth", depth)

            await asyncio.sleep(interval)

    # -- live transport -------------------------------------------------------
    @property
    def live_streams(self) -> list[str]:
        """Binance combined-stream names for this symbol."""
        sym = self._state.symbol.lower()
        return [f"{sym}@aggTrade", f"{sym}@depth20@100ms"]

    @property
    def live_url(self) -> str:
        """Combined-stream endpoint derived from the configured WS base URL."""
        base = self._env.binance_ws_base_url.rstrip("/")
        # Promote a single-stream base (".../ws") to the combined endpoint.
        if base.endswith("/ws"):
            base = base[: -len("/ws")]
        return f"{base}/stream?streams={'/'.join(self.live_streams)}"

    async def _run_live(self) -> None:
        """Connect to Binance combined streams with auto-reconnect.

        Frames are normalised to the same ``md.aggTrade`` / ``md.depth`` topics
        the mock path uses, so every downstream consumer is transport-agnostic.
        Ping/Pong keepalive is handled by the websockets client (Binance pings;
        the client auto-pongs) plus a client-side ``ping_interval``.
        """
        try:
            import websockets  # lazy import keeps the mock path dependency-free
        except ImportError:
            await self._bus.publish(
                "system.log",
                level="danger",
                source="streamer",
                message="websockets not installed -- cannot run LIVE mode",
            )
            return

        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(
                    self.live_url, ping_interval=180, ping_timeout=10, max_queue=1024
                ) as ws:
                    backoff = 1.0  # reset on a clean connect
                    await self._bus.publish(
                        "system.log",
                        level="ok",
                        source="streamer",
                        message=f"LIVE connected: {self.symbol}",
                    )
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._on_live_frame(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 -- reconnect on any transport error
                await self._bus.publish(
                    "system.log",
                    level="warn",
                    source="streamer",
                    message=f"LIVE disconnected ({exc}); retrying in {backoff:.0f}s",
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)  # capped exponential backoff

    async def _on_live_frame(self, raw: str | bytes) -> None:
        """Unwrap a Binance combined-stream frame and route it onto the bus."""
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        stream = msg.get("stream", "")
        data = msg.get("data", msg)
        if "aggTrade" in stream or data.get("e") == "aggTrade":
            await self._emit("md.aggTrade", data)
        elif "depth" in stream or "bids" in data:
            await self._emit("md.depth", data)

    # -- price / book synthesis ----------------------------------------------
    def _advance_price(self) -> None:
        s = self._state
        # apply any queued flash-crash shock first
        if s.pending_shock_pct:
            s.mid *= 1.0 + s.pending_shock_pct
            s.momentum = s.pending_shock_pct  # imprint the shock on momentum
            s.pending_shock_pct = 0.0
        # slowly-wandering drift creates realistic trending stretches
        # (mean-reverting random walk on the drift itself)
        s.drift = max(-0.0003, min(0.0003, 0.97 * s.drift + random.gauss(0.0, 0.00005)))
        # geometric random walk around the current drift
        shock = random.gauss(s.drift, s.vol * s.vol_multiplier)
        s.mid = max(1.0, s.mid * (1.0 + shock))
        # smoothed momentum (EWMA of returns) drives the order-book skew
        s.momentum = 0.85 * s.momentum + 0.15 * shock
        # decay volatility back toward baseline
        if s.vol_multiplier > 1.0:
            s.vol_multiplier = max(1.0, s.vol_multiplier * 0.97)

    def _now_ms(self) -> int:
        now = int(time.time() * 1000)
        if self._state.stale_frames > 0:
            now -= self._state.stale_lag_ms
            self._state.stale_frames -= 1
        return now

    def _gen_agg_trades(self) -> list[dict[str, Any]]:
        """Produce 1-4 aggTrade frames in exact Binance spot schema."""
        s = self._state
        frames: list[dict[str, Any]] = []
        # Momentum biases the aggressor mix: uptrend => more aggressive buys.
        bias = max(-0.35, min(0.35, s.momentum * 300.0))
        for _ in range(random.randint(1, 4)):
            s.agg_id += 1
            # taker side tied to momentum (True => aggressive SELL / buyer is maker)
            is_buyer_maker = random.random() < (0.5 - bias)
            price = s.mid + random.uniform(-s.tick_size, s.tick_size)
            qty = round(abs(random.gauss(0.05, 0.04)) + 0.001, 6)
            ts = self._now_ms()
            frames.append(
                {
                    "e": "aggTrade",
                    "E": ts,
                    "s": s.symbol,
                    "a": s.agg_id,
                    "p": f"{price:.2f}",
                    "q": f"{qty:.6f}",
                    "f": s.agg_id,
                    "l": s.agg_id,
                    "T": ts,
                    "m": is_buyer_maker,
                    "M": True,
                }
            )
        return frames

    def _gen_depth(self) -> dict[str, Any]:
        """Produce a top-20 partial book in exact Binance spot schema."""
        s = self._state
        s.update_id += 1
        bids: list[list[str]] = []
        asks: list[list[str]] = []
        # Momentum skews resting liquidity: in an uptrend bids stack up (OBI > 0),
        # in a downtrend asks dominate (OBI < 0). Bounded so OBI stays in range.
        skew = max(-0.6, min(0.6, s.momentum * 400.0))
        bid_scale = 1.0 + skew
        ask_scale = 1.0 - skew
        for level in range(DEPTH_LEVELS):
            bid_price = s.mid - s.tick_size * (level + 1)
            ask_price = s.mid + s.tick_size * (level + 1)
            # liquidity thins out away from the touch
            depth_decay = max(0.05, 1.0 - level * 0.03)
            bid_qty = round(abs(random.gauss(s.base_qty, 0.25)) * depth_decay * bid_scale + 0.01, 4)
            ask_qty = round(abs(random.gauss(s.base_qty, 0.25)) * depth_decay * ask_scale + 0.01, 4)
            bids.append([f"{bid_price:.2f}", f"{bid_qty:.4f}"])
            asks.append([f"{ask_price:.2f}", f"{ask_qty:.4f}"])
        return {"lastUpdateId": s.update_id, "bids": bids, "asks": asks}

    # -- publish --------------------------------------------------------------
    async def _emit(self, topic: str, frame: dict[str, Any]) -> None:
        if self._queue.full():
            self._queue.get_nowait()  # drop oldest under back-pressure
        self._queue.put_nowait(frame)
        await self._bus.publish(topic, symbol=self._state.symbol, frame=frame)
