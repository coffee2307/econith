"""ECONITH :: bridges.quant_bridge

QUANT DOMAIN BRIDGE — the strict state-isolation execution gate that unifies the
legacy ``ExchangeBridge`` (mock TWAP UI telemetry) with the advanced
:class:`~quant.ccxt_bridge.CCXTBinanceBridge` (capital-bearing executor).

The Sentinel-gated ``ai.signal -> order.intent`` chain is unchanged. This bridge
subscribes to the authoritative ``order.intent`` topic and routes execution
purely on the active :class:`~core.mode.QuantMode`:

    REALITY     -> CCXTBinanceBridge live Binance session; publishes ``quant.fill``
    SIMULATION  -> CCXTBinanceBridge synthetic fills;       publishes ``quant.fill``

In both modes the cockpit flight-log is fed from a single, authoritative
``quant.fill`` stream, eliminating the previous ledger duplication. The legacy
``ExchangeBridge`` may continue to run in parallel for its ``order.update`` feed
without polluting capital state, because only the CCXT path (REALITY) ever
touches the exchange.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from core.event_bus import DOMAIN_QUANT, Event, EventBus
from core.ingestion.context_state import AssetUniverse
from core.mode import QuantMode, current_mode
from quant.ccxt_bridge import CCXTBinanceBridge
from quant.payloads import ExecutionPayload, OrderSide, OrderType

logger = logging.getLogger("econith.bridges.quant")

__all__ = ["QuantExecutionBridge"]

# Fallback desk for symbols outside the strict asset universe.
_DEFAULT_DESK = "crypto_majors"


class QuantExecutionBridge:
    """Single authority converting ``order.intent`` into mode-routed fills."""

    def __init__(
        self,
        bus: EventBus,
        ccxt: CCXTBinanceBridge,
        *,
        base_notional: float = 1_000.0,
    ) -> None:
        self._bus = bus
        self._ccxt = ccxt
        self._base_notional = base_notional
        self._marks: dict[str, float] = {}

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        # DOMAIN_QUANT tags this as an order-routing node so the EventBus
        # governance layer hard-blocks any simulated ``world.*`` topic from
        # ever reaching live execution while running in REALITY.
        self._bus.subscribe("order.intent", self._on_intent, domain=DOMAIN_QUANT)
        self._bus.subscribe("md.ticker", self._on_ticker, domain=DOMAIN_QUANT)
        logger.info("quant execution bridge registered (mode-gated CCXT routing)")

    async def _on_ticker(self, event: Event) -> None:
        sym = event.payload.get("symbol")
        price = event.payload.get("price")
        if sym and price is not None:
            symbol = str(sym).upper()
            self._marks[symbol] = float(price)
            # Keep the CCXT synthetic engine priced against the live mark.
            self._ccxt.update_sim_mark(symbol, float(price))

    # -- routing gate ---------------------------------------------------------
    async def _on_intent(self, event: Event) -> None:
        payload = self._build_payload(event.payload)
        if payload is None:
            return
        mode = current_mode()
        # The CCXTBinanceBridge internally honours the same gate: it executes
        # against the live session only in REALITY (and when connected), else it
        # produces a deterministic synthetic fill. Both paths publish quant.fill.
        report = await self._ccxt.execute(payload)
        logger.debug(
            "routed %s %s qty=%.6f @ %.4f (%s)",
            mode.value, payload.symbol, report.filled_quantity, report.fill_price,
            "LIVE" if mode is QuantMode.REALITY else "SYNTHETIC",
        )

    # -- intent -> execution payload -----------------------------------------
    def _build_payload(self, intent: dict) -> ExecutionPayload | None:
        symbol = str(intent.get("symbol", "BTCUSDT")).upper()
        quantity = float(intent.get("quantity", 0.0))
        if quantity <= 0.0:
            return None
        side = self._resolve_side(
            str(intent.get("side", "BUY")), bool(intent.get("reduce_only", False))
        )
        desk = self._resolve_desk(symbol)
        try:
            return ExecutionPayload(
                symbol=symbol,
                desk=desk,
                mode=current_mode().value,
                side=side,
                order_type=OrderType.MARKET,
                quantity=quantity,
                confidence=self._extract_confidence(intent),
                macro_regime=self._extract_regime(intent),
                client_order_id=self._client_id(symbol),
            )
        except Exception:  # noqa: BLE001 - a malformed intent must not desync the bus
            logger.exception("failed to build execution payload for %s", symbol)
            return None

    @staticmethod
    def _resolve_side(side_str: str, reduce_only: bool) -> OrderSide:
        buy = side_str.upper() == "BUY"
        if buy and not reduce_only:
            return OrderSide.LONG_OPEN
        if not buy and reduce_only:
            return OrderSide.LONG_CLOSE
        if not buy and not reduce_only:
            return OrderSide.SHORT_OPEN
        return OrderSide.SHORT_CLOSE

    @staticmethod
    def _resolve_desk(symbol: str) -> str:
        try:
            return AssetUniverse.desk_of(symbol).value
        except KeyError:
            return _DEFAULT_DESK

    @staticmethod
    def _extract_confidence(intent: dict) -> float:
        reason = str(intent.get("reason", ""))
        for token in reason.split():
            if token.startswith("conf="):
                try:
                    return max(0.0, min(1.0, float(token.split("=", 1)[1])))
                except ValueError:
                    return 0.0
        return 0.0

    @staticmethod
    def _extract_regime(intent: dict) -> str:
        reason = str(intent.get("reason", ""))
        for token in reason.split():
            if token.startswith("regime="):
                return token.split("=", 1)[1]
        return "UNKNOWN"

    @staticmethod
    def _client_id(symbol: str) -> str:
        seed = f"{symbol}:{datetime.now(timezone.utc).timestamp()}"
        digest = hashlib.sha1(seed.encode()).hexdigest()[:12]
        return f"ECN-{symbol[:6]}-{digest}"
