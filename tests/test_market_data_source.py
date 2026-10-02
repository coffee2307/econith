from __future__ import annotations

import asyncio
from types import SimpleNamespace

import infrastructure.websocket.streamer as streamer_module
from infrastructure.websocket.streamer import BinanceWebSocketStreamer


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, topic: str, **payload) -> None:
        self.events.append((topic, payload))


def _environment(mode: str = "live") -> SimpleNamespace:
    return SimpleNamespace(
        binance_market_data_mode=mode,
        binance_data_ws_base_url="wss://stream.binance.com:9443/ws",
    )


def test_public_binance_feed_is_default_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(streamer_module, "get_environment", lambda: _environment())
    streamer = BinanceWebSocketStreamer(_Bus(), SimpleNamespace(running=True, multiplier=1))

    assert streamer.is_mock is False
    assert streamer.data_source == "binance_public"
    assert streamer.live_url == (
        "wss://stream.binance.com:9443/stream?"
        "streams=btcusdt@aggTrade/btcusdt@depth20@100ms"
    )


def test_mock_feed_requires_explicit_configuration(monkeypatch) -> None:
    monkeypatch.setattr(
        streamer_module, "get_environment", lambda: _environment("mock")
    )
    streamer = BinanceWebSocketStreamer(_Bus(), SimpleNamespace(running=True, multiplier=1))

    assert streamer.is_mock is True
    assert streamer.data_source == "synthetic_mock"


def test_published_frames_include_source_provenance(monkeypatch) -> None:
    monkeypatch.setattr(streamer_module, "get_environment", lambda: _environment())
    bus = _Bus()
    streamer = BinanceWebSocketStreamer(bus, SimpleNamespace(running=True, multiplier=1))

    asyncio.run(streamer._emit("md.aggTrade", {"p": "85000.00"}))

    assert bus.events[-1][1]["source"] == "binance_public"
    assert streamer.status()["connected"] is True
