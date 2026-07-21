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
from econith.world import AbidesStepKernel
from quant.ccxt_bridge import CCXTBinanceBridge
from quant.payloads import ExecutionPayload, OrderSide, OrderType
from quant.pretrade_gate import PreTradeGate, edge_bps_from_signal

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
        abides: AbidesStepKernel | None = None,
    ) -> None:
        self._bus = bus
        self._ccxt = ccxt
        self._base_notional = base_notional
        self._marks: dict[str, float] = {}
        # Mandatory pre-trade net-profit filter (Anti-Overtrading Protocol).
        self._gate = PreTradeGate()
        # Optional ABIDES synthetic LOB. When present AND in SIMULATION, order
        # intents fill against the discrete order book instead of the CCXT
        # synthetic path. In REALITY it is never consulted (defense-in-depth on
        # top of the shim's own SIMULATION-only guard).
        self._abides = abides if abides is not None else AbidesStepKernel()

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        # DOMAIN_QUANT tags this as an order-routing node so the EventBus
        # governance layer hard-blocks any simulated ``world.*`` topic from
        # ever reaching live execution while running in REALITY.
        self._bus.subscribe("order.intent", self._on_intent, domain=DOMAIN_QUANT)
        self._bus.subscribe("md.ticker", self._on_ticker, domain=DOMAIN_QUANT)
        # The ABIDES tape consumers (md.depth/md.aggTrade) are ungoverned — they
        # only shape the synthetic book, never touch live routing.
        if self._abides is not None:
            try:
                self._abides.bind(self._bus)
            except Exception:  # noqa: BLE001
                logger.debug("abides kernel bind skipped")
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

        # === ORDER EXECUTION GATE (mandatory net-profit filter) ==============
        # Expected Net Profit = Expected Gross - Fees - Slippage/Impact - Spread.
        # A non-positive expectancy (or a risk-budget breach) drops the order.
        gate = self._evaluate_pretrade(event.payload, payload)
        if gate is not None and not gate.approved:
            await self._bus.publish(
                "order.rejected",
                symbol=payload.symbol,
                side=payload.side.value,
                quantity=payload.quantity,
                reason=gate.reason,
                gate=gate.as_dict(),
            )
            await self._bus.publish(
                "system.log", level="info", source="pretrade_gate",
                message=(
                    f"DROP {payload.symbol} {payload.side.value} — {gate.reason} "
                    f"(net={gate.expected_net:.6f}, fees={gate.expected_fees:.6f})"
                ),
            )
            return

        mode = current_mode()
        # SIMULATION + ABIDES available -> fill against the discrete LOB simulator
        # for realistic microstructure. Any failure degrades to the CCXT synthetic
        # path so a fill is never dropped.
        if mode is QuantMode.SIMULATION and self._abides is not None:
            try:
                await self._abides.submit(
                    symbol=payload.symbol,
                    side=payload.side.value if hasattr(payload.side, "value") else str(payload.side),
                    quantity=payload.quantity,
                    client_order_id=payload.client_order_id,
                )
                logger.debug("routed SIMULATION %s via native ABIDES kernel", payload.symbol)
                return
            except Exception:  # noqa: BLE001 - fall through to CCXT synthetic
                logger.debug("abides submit failed; CCXT synthetic fallback")
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

    # -- pre-trade net-profit gate -------------------------------------------
    def _evaluate_pretrade(self, intent: dict, payload: ExecutionPayload):
        """Score a candidate order against the net-profit protocol.

        Returns ``None`` (no opinion — fail-open) when there is no live mark to
        price the order, so the gate never blocks purely for lack of a quote.
        """
        mark = self._marks.get(payload.symbol)
        if not mark or mark <= 0.0:
            return None
        direction = self._extract_float(intent, "dir=")
        confidence = self._extract_confidence(intent)
        regime = self._extract_regime(intent)
        edge_bps = edge_bps_from_signal(direction, confidence, regime)
        side = "BUY" if payload.side.value.startswith("LONG") else "SELL"
        return self._gate.evaluate(
            price=mark,
            quantity=payload.quantity,
            side=side,
            expected_edge_bps=edge_bps,
            edge_source="estimated",
        )

    @staticmethod
    def _extract_float(intent: dict, prefix: str) -> float:
        reason = str(intent.get("reason", ""))
        for token in reason.split():
            if token.startswith(prefix):
                try:
                    return float(token.split("=", 1)[1])
                except ValueError:
                    return 0.0
        return 0.0

    @staticmethod
    def _client_id(symbol: str) -> str:
        seed = f"{symbol}:{datetime.now(timezone.utc).timestamp()}"
        digest = hashlib.sha1(seed.encode()).hexdigest()[:12]
        return f"ECN-{symbol[:6]}-{digest}"
