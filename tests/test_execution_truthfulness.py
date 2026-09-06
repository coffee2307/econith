"""Execution truthfulness: no fake fills, reduce_only, mock TWAP off."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_mock_twap_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ECONITH_MOCK_TWAP", raising=False)
    from econith_quant.bridge.exchange_bridge import ExchangeBridge, mock_twap_enabled

    assert mock_twap_enabled() is False
    bus = MagicMock()
    bridge = ExchangeBridge(bus)
    bridge.register()
    bus.subscribe.assert_not_called()


def test_mock_twap_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ECONITH_MOCK_TWAP", "true")
    from econith_quant.bridge.exchange_bridge import ExchangeBridge, mock_twap_enabled

    assert mock_twap_enabled() is True
    bus = MagicMock()
    bridge = ExchangeBridge(bus, enabled=True)
    bridge.register()
    assert bus.subscribe.call_count >= 2


def test_aibridge_sets_reduce_only_when_reducing_exposure() -> None:
    """When target exposure shrinks, intents must carry reduce_only=True."""
    from econith_quant.bridge.ai_bridge import AIBridge

    published: list[tuple[str, dict]] = []

    class Bus:
        def subscribe(self, *_a, **_k):
            return None

        async def publish(self, topic: str, **payload):
            published.append((topic, payload))

    bridge = AIBridge(Bus())  # type: ignore[arg-type]
    bridge._target_exposure = 0.8
    bridge._mode = "NORMAL"
    bridge._min_delta = 0.001

    async def _run():
        # Move from +0.8 toward a smaller long — still produces route legs.
        await bridge._on_signal(
            type(
                "E",
                (),
                {
                    "payload": {
                        "action": "LONG",
                        "direction": 0.2,
                        "confidence": 0.9,
                        "regime": "TREND",
                    }
                },
            )()
        )

    asyncio.run(_run())
    intents = [p for t, p in published if t == "order.intent"]
    assert intents, published
    assert all(p.get("reduce_only") is True for p in intents)


def test_live_execute_failure_is_rejected_not_synthetic(monkeypatch) -> None:
    import quant.ccxt_bridge as bridge_mod
    from core.mode import QuantMode
    from quant.ccxt_bridge import CCXTBinanceBridge, FillReport
    from quant.payloads import ExecutionPayload, OrderSide, OrderType

    monkeypatch.setattr(bridge_mod, "current_mode", lambda: QuantMode.REALITY)

    published: list[tuple[str, dict]] = []

    class Bus:
        async def publish(self, topic: str, **payload):
            published.append((topic, dict(payload)))

    bridge = CCXTBinanceBridge.__new__(CCXTBinanceBridge)
    bridge._bus = Bus()
    bridge._live = True
    bridge._exchange = object()
    bridge._credentialed = True
    bridge._execution_env = "demo"
    bridge._testnet = True
    bridge._default_type = "future"
    bridge._taker_fee_bps = 4.0
    bridge._sim_marks = {"BTCUSDT": 60_000.0}

    async def _boom(_payload):
        raise TimeoutError("exchange timed out")

    bridge._execute_reality = _boom  # type: ignore[method-assign]
    bridge._publish_execution_status = AsyncMock()  # type: ignore[method-assign]
    bridge._publish_wallet_truth = AsyncMock()  # type: ignore[method-assign]

    payload = ExecutionPayload(
        symbol="BTCUSDT",
        desk="trend",
        mode="REALITY",
        side=OrderSide.LONG_OPEN,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )

    report = asyncio.run(bridge.execute(payload))
    assert isinstance(report, FillReport)
    assert report.status == "UNKNOWN"
    assert report.filled_quantity == 0.0
    assert report.is_filled is False
    assert not any(t == "quant.fill" for t, _ in published)
    assert any(t == "order.update" and p.get("status") == "REJECTED" for t, p in published)


def test_fill_report_rejected_factory() -> None:
    from quant.ccxt_bridge import FillReport
    from quant.payloads import ExecutionPayload, OrderSide, OrderType

    payload = ExecutionPayload(
        symbol="BTCUSDT",
        desk="trend",
        mode="REALITY",
        side=OrderSide.LONG_OPEN,
        order_type=OrderType.MARKET,
        quantity=0.01,
    )
    report = FillReport.rejected(payload, reason="InsufficientMargin", status="REJECTED")
    assert report.is_filled is False
    entry = report.to_ledger_entry()
    assert entry["filledVolume"] == 0.0
    assert entry["status"] == "REJECTED"
