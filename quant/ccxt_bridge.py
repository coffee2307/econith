"""ECONITH :: quant.ccxt_bridge

Binance production & simulation execution bridge via the open-source CCXT
framework.

Two sovereign, state-isolated modes:

* ``REALITY``  -- authenticates a live Binance Spot/Futures session through CCXT
  and routes real orders. Hard-sandboxed from any WORLD simulation variable.
* ``SIMULATION`` -- decouples from the exchange entirely and fills against the
  synthetic vectors emitted by the WORLD engine, so macro stress-tests never
  touch live capital.

The bridge accepts deterministic :class:`ExecutionPayload` intents (produced by
the :class:`~quant.context_slicer.DeskPolicyHead`), lowers them to CCXT order
kwargs and publishes fill telemetry onto the EventBus for the cockpit ledger.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from core.event_bus import EventBus
from core.mode import QuantMode, current_mode, get_mode_manager
from quant.payloads import (
    CCXTOrderPayload,
    ExecutionPayload,
    OrderType,
)

logger = logging.getLogger("econith.quant.ccxt_bridge")

__all__ = ["FillReport", "CCXTBinanceBridge"]


# CCXT unified symbol mapping for the crypto desks (Binance perp/spot).
_CCXT_SYMBOL: dict[str, str] = {
    "BTCUSDT": "BTC/USDT",
    "ETHUSDT": "ETH/USDT",
    "SOLUSDT": "SOL/USDT",
    "AVAXUSDT": "AVAX/USDT",
    "NEARUSDT": "NEAR/USDT",
    "SUIUSDT": "SUI/USDT",
    "DOGEUSDT": "DOGE/USDT",
    "SHIBUSDT": "SHIB/USDT",
    "PEPEUSDT": "PEPE/USDT",
}


@dataclass(slots=True)
class FillReport:
    """The realised outcome of an execution, streamed to the cockpit ledger."""

    order_id: str
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    filled_quantity: float
    fill_price: float
    slippage_delta: float
    commission: float
    mode: str
    ts: datetime

    def to_ledger_entry(self) -> dict[str, object]:
        """Shape exactly matching the cockpit ``IMatchedOrderLog`` contract."""
        return {
            "orderId": self.order_id,
            "clientOrderId": self.client_order_id,
            "timestampUs": int(self.ts.timestamp() * 1_000_000),
            "asset": self.symbol,
            "side": self.side,
            "executionType": self.order_type,
            "filledVolume": self.filled_quantity,
            "fillPrice": self.fill_price,
            "slippageDelta": self.slippage_delta,
            "commission": self.commission,
            "mode": self.mode,
        }


class CCXTBinanceBridge:
    """State-isolated execution bridge over CCXT Binance."""

    def __init__(
        self,
        bus: EventBus,
        *,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
        default_type: str = "future",
        taker_fee_bps: float = 4.0,
        credentialed: bool = True,
    ) -> None:
        self._bus = bus
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._default_type = default_type
        self._taker_fee_bps = taker_fee_bps
        # Only authenticate a live session when REAL trade credentials exist.
        # With placeholder/empty keys the bridge stays mock-first (synthetic
        # fills) even in REALITY mode, so the platform boots without a live
        # exchange round-trip or a blocking ``load_markets`` network call.
        self._credentialed = credentialed
        self._exchange: object | None = None
        # ``True`` only after a live CCXT session is fully authenticated AND its
        # markets have loaded. Any DNS/network/CCXT fault flips this back to
        # ``False`` so ``execute`` routes to the synthetic path (fault isolation).
        self._live: bool = False
        self._sim_marks: dict[str, float] = {}
        # Air-gap guard: armed once so a REALITY->SIMULATION transition force
        # tears down any live exchange socket instead of leaving it bound.
        self._mode_guard_armed: bool = False

    # -- lifecycle ------------------------------------------------------------
    def _arm_mode_guard(self) -> None:
        """Register a one-shot listener that air-gaps live sockets on mode exit."""
        if self._mode_guard_armed:
            return
        self._mode_guard_armed = True
        get_mode_manager().on_change(self._on_mode_change)

    def _on_mode_change(self, prev: QuantMode, new: QuantMode) -> None:
        """Force-drop any live session the instant we leave REALITY (air-gap)."""
        if new is QuantMode.REALITY:
            return
        if self._exchange is None and not self._live:
            return
        logger.warning(
            "[QUANT BRIDGE] mode %s -> %s: air-gapping live Binance session",
            prev.value, new.value,
        )
        exchange, self._exchange = self._exchange, None
        self._live = False
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._safe_dispose(exchange))
        except RuntimeError:
            # No running loop (e.g. sync test context): best-effort, drop ref.
            logger.debug("no running loop to dispose CCXT session; reference dropped")

    async def connect(self) -> None:
        """Authenticate a live CCXT session, degrading safely on any fault.

        This method is a hard uptime boundary: it MUST return successfully so the
        ASGI lifespan and every other subsystem boot to completion. It never
        propagates an exception. When REALITY connectivity is unavailable
        (offline host, DNS hitch, unreachable testnet, missing/invalid keys, or
        an explicit SIMULATION mode) it transparently falls back to local
        synthetic fills.
        """
        self._live = False
        # Arm the air-gap guard on every boot so a later mode switch is caught.
        self._arm_mode_guard()

        # --- explicit SIMULATION or no credentials: never touch the network ---
        if current_mode() is not QuantMode.REALITY:
            logger.info("CCXT bridge idle -- SIMULATION mode uses synthetic fills")
            return
        if not self._credentialed:
            logger.info(
                "CCXT bridge staying synthetic -- no real Binance trade credentials"
            )
            return

        # --- REALITY: attempt a live session, isolating every failure mode ----
        try:
            import ccxt.async_support as ccxt
        except ImportError:
            logger.warning("ccxt not installed -- REALITY execution unavailable")
            return

        exchange: object | None = None
        try:
            exchange = ccxt.binance(
                {
                    "apiKey": self._api_key,
                    "secret": self._api_secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": self._default_type},
                }
            )
            if self._testnet:
                exchange.set_sandbox_mode(True)  # type: ignore[attr-defined]
            # The network-facing call: DNS/connectivity failures surface here.
            await exchange.load_markets()  # type: ignore[attr-defined]
        except (ccxt.ExchangeNotAvailable, ccxt.NetworkError) as exc:
            logger.warning(
                "[QUANT BRIDGE] Target exchange unreachable due to DNS/Network "
                "error. Gracefully falling back to local SIMULATION mode. (%s)",
                exc,
            )
            await self._safe_dispose(exchange)
            return
        except Exception as exc:  # noqa: BLE001 - any startup fault must isolate
            logger.warning(
                "[QUANT BRIDGE] Live Binance session failed to initialise "
                "(%s: %s). Gracefully falling back to local SIMULATION mode.",
                type(exc).__name__, exc,
            )
            await self._safe_dispose(exchange)
            return

        self._exchange = exchange
        self._live = True
        logger.info("CCXT Binance session authenticated (testnet=%s)", self._testnet)

    @staticmethod
    async def _safe_dispose(exchange: object | None) -> None:
        """Release a half-open CCXT session's aiohttp resources, never raising."""
        if exchange is None:
            return
        try:
            await exchange.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - disposal must never crash startup
            logger.debug("CCXT session disposal raised during fallback", exc_info=True)

    async def close(self) -> None:
        self._live = False
        if self._exchange is not None:
            await self._safe_dispose(self._exchange)
            self._exchange = None

    @property
    def is_live(self) -> bool:
        """True only when a live, markets-loaded CCXT session is active."""
        return self._live and self._exchange is not None

    # -- synthetic marks (SIMULATION) -----------------------------------------
    def update_sim_mark(self, symbol: str, price: float) -> None:
        """Feed a synthetic mark price from the WORLD engine."""
        self._sim_marks[symbol.upper()] = price

    # -- execution ------------------------------------------------------------
    async def execute(self, payload: ExecutionPayload) -> FillReport:
        """Route an execution payload through the mode-appropriate path.

        Routes live ONLY when in REALITY mode with a fully-authenticated session
        (``is_live``); otherwise (SIMULATION, or a REALITY bridge that degraded
        on a network fault at startup) it produces a deterministic synthetic
        fill. A live order that raises mid-session degrades to synthetic rather
        than propagating, preserving the platform's uptime invariant.
        """
        if current_mode() is QuantMode.REALITY and self.is_live:
            try:
                report = await self._execute_reality(payload)
            except Exception as exc:  # noqa: BLE001 - never break the exec loop
                logger.warning(
                    "[QUANT BRIDGE] Live execution failed (%s: %s); "
                    "falling back to synthetic fill for %s.",
                    type(exc).__name__, exc, payload.symbol,
                )
                report = self._execute_simulation(payload)
        else:
            report = self._execute_simulation(payload)
        await self._bus.publish("quant.fill", **report.to_ledger_entry())
        return report

    async def _execute_reality(self, payload: ExecutionPayload) -> FillReport:
        ccxt_symbol = _CCXT_SYMBOL.get(payload.symbol, payload.symbol)
        order_kwargs = CCXTOrderPayload.from_execution(payload, ccxt_symbol)
        try:
            result = await self._exchange.create_order(  # type: ignore[attr-defined]
                symbol=order_kwargs.symbol,
                type=order_kwargs.type,
                side=order_kwargs.side,
                amount=order_kwargs.amount,
                price=order_kwargs.price,
                params=order_kwargs.params,
            )
        except Exception:  # noqa: BLE001 - surface as a rejected fill, never crash
            logger.exception("CCXT order rejected for %s", payload.symbol)
            raise
        avg = float(result.get("average") or result.get("price") or 0.0)
        filled = float(result.get("filled") or payload.quantity)
        fee = self._extract_fee(result, filled, avg)
        ref = payload.limit_price or avg
        slippage = (avg - ref) if ref else 0.0
        return FillReport(
            order_id=str(result.get("id", "")),
            client_order_id=payload.client_order_id,
            symbol=payload.symbol,
            side=payload.side.value,
            order_type=payload.order_type.value,
            filled_quantity=filled,
            fill_price=avg,
            slippage_delta=slippage,
            commission=fee,
            mode="REALITY",
            ts=datetime.now(timezone.utc),
        )

    def _execute_simulation(self, payload: ExecutionPayload) -> FillReport:
        # Fill against the synthetic mark with a deterministic micro-slippage
        # model proportional to size vs a nominal depth.
        mark = self._sim_marks.get(payload.symbol, payload.limit_price or 1.0)
        depth_ref = 50.0  # nominal book depth in base units
        impact = min(0.002, payload.quantity / (depth_ref * 1_000.0))
        direction = 1.0 if payload.side.ccxt_side == "buy" else -1.0
        fill_price = mark * (1.0 + direction * impact)
        commission = fill_price * payload.quantity * (self._taker_fee_bps / 10_000.0)
        return FillReport(
            order_id=f"SIM-{int(datetime.now(timezone.utc).timestamp()*1e6)}",
            client_order_id=payload.client_order_id,
            symbol=payload.symbol,
            side=payload.side.value,
            order_type=payload.order_type.value,
            filled_quantity=payload.quantity,
            fill_price=round(fill_price, 8),
            slippage_delta=round(fill_price - mark, 8),
            commission=round(commission, 8),
            mode="SIMULATION",
            ts=datetime.now(timezone.utc),
        )

    def _extract_fee(self, result: dict, filled: float, avg: float) -> float:
        fee = result.get("fee") or {}
        cost = fee.get("cost") if isinstance(fee, dict) else None
        if cost is not None:
            return float(cost)
        # Fall back to the configured taker fee estimate.
        return avg * filled * (self._taker_fee_bps / 10_000.0)

    async def execute_algo(self, payload: ExecutionPayload) -> list[FillReport]:
        """Execute a TWAP/VWAP payload as its scheduled child slices."""
        if not payload.slices:
            return [await self.execute(payload)]
        reports: list[FillReport] = []
        for child in payload.slices:
            await asyncio.sleep(child.scheduled_offset_ms / 1000.0)
            child_payload = payload.model_copy(
                update={"quantity": child.quantity, "slices": (), "order_type": OrderType.MARKET}
            )
            reports.append(await self.execute(child_payload))
        return reports
