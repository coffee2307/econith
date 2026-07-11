"""ECONITH :: core.cockpit.ws

Aviation-cockpit telemetry read-model + FastAPI WebSocket endpoints.

:class:`CockpitTelemetryHub` subscribes to the EventBus (``quant.fill``,
``core.macro.context``, ``world.macro``, ...), maintains the rolling cockpit
state (flight log, PnL HUD, margin matrix, allocation radar) and serialises a
:class:`CockpitTelemetryFrame` on demand.

:func:`build_cockpit_router` mounts the non-blocking WebSocket that pumps frames
to the Next.js cockpit at high frequency, plus a REST snapshot for cold loads.
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.cockpit.schemas import (
    AllocationCell,
    AssetAllocationRadar,
    CockpitTelemetryFrame,
    DeskTier,
    MacroContextStrip,
    MarginSecurityMatrix,
    MatchedOrderLog,
    PnLTelemetryHUD,
)
from core.event_bus import Event, EventBus
from core.ingestion.context_state import AssetUniverse
from core.mode import get_mode_manager

logger = logging.getLogger("econith.core.cockpit.ws")

__all__ = ["CockpitTelemetryHub", "build_cockpit_router"]

_MAX_LEDGER = 200
_MAX_EQUITY = 300


class CockpitTelemetryHub:
    """Consolidated cockpit read-model driven by the EventBus."""

    def __init__(self, bus: EventBus, starting_capital: float = 100_000.0) -> None:
        self._bus = bus
        self._starting_capital = starting_capital
        self._ledger: deque[MatchedOrderLog] = deque(maxlen=_MAX_LEDGER)
        self._equity: deque[float] = deque([starting_capital], maxlen=_MAX_EQUITY)
        self._realized_session = 0.0
        self._realized_total = 0.0
        self._unrealized = 0.0
        self._wins = 0
        self._losses = 0
        self._gross_profit = 0.0
        self._gross_loss = 0.0
        self._peak_equity = starting_capital
        self._max_drawdown = 0.0
        self._returns: deque[float] = deque(maxlen=_MAX_EQUITY)
        self._positions: dict[str, dict[str, float]] = {}
        self._marks: dict[str, float] = {}
        self._macro: dict[str, float | None] = {
            "fed_funds": None, "dxy": None, "gold": None
        }
        self._regime = ("UNKNOWN", 0.0)
        self._sim_day = 0

    # -- wiring ---------------------------------------------------------------
    def register(self) -> None:
        self._bus.subscribe("quant.fill", self._on_fill)
        self._bus.subscribe("quant.capital.sync", self._on_capital_sync)
        self._bus.subscribe("quant.wallet.sync", self._on_wallet_sync)
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("core.macro.context", self._on_macro)
        self._bus.subscribe("world.macro", self._on_world)
        self._bus.subscribe("world.sovereign", self._on_world)
        self._bus.subscribe("ai.signal", self._on_ai_signal)

    # -- handlers -------------------------------------------------------------
    async def _on_fill(self, event: Event) -> None:
        p = event.payload
        try:
            entry = MatchedOrderLog.model_validate(p)
        except Exception:  # noqa: BLE001 - a malformed fill must not desync the hub
            logger.exception("bad fill payload")
            return
        self._ledger.appendleft(entry)
        self._apply_pnl(entry)

    async def _on_capital_sync(self, event: Event) -> None:
        """Re-base equity from the live Binance demo wallet (boot-time only)."""
        if self._positions or self._ledger:
            return
        base = float(event.payload.get("equity_base", 0))
        if base <= 0:
            return
        self._starting_capital = base
        self._equity.clear()
        self._equity.append(base)
        self._peak_equity = base
        self._realized_total = 0.0
        self._unrealized = 0.0
        logger.info("cockpit equity base synced to %.2f USDT", base)

    async def _on_wallet_sync(self, event: Event) -> None:
        """Exchange wallet is the PnL source of truth after live fills."""
        p = event.payload
        equity = float(p.get("equity", 0))
        if equity <= 0:
            return
        upnl = float(p.get("unrealized_pnl", 0))
        raw_positions = p.get("positions") or {}
        self._positions = {
            str(sym).upper(): {
                "qty": float(pos.get("qty", 0)),
                "avg": float(pos.get("avg", 0)),
            }
            for sym, pos in raw_positions.items()
        }
        self._unrealized = upnl
        self._realized_total = equity - self._starting_capital - upnl
        self._update_equity()

    async def _on_ticker(self, event: Event) -> None:
        sym = event.payload.get("symbol")
        price = event.payload.get("price")
        if sym and price is not None:
            self._marks[str(sym).upper()] = float(price)
            self._recompute_unrealized()

    async def _on_macro(self, event: Event) -> None:
        macro = event.payload.get("macro", {}) or {}
        self._macro["fed_funds"] = macro.get("fed_funds_effective_rate")
        self._macro["dxy"] = macro.get("dollar_index_dxy")
        self._macro["gold"] = macro.get("gold_xau_spot")
        label = event.payload.get("regime_label")
        if label:
            self._regime = (label, self._regime[1])

    async def _on_world(self, event: Event) -> None:
        self._sim_day = int(event.payload.get("sim_day", self._sim_day))

    async def _on_ai_signal(self, event: Event) -> None:
        label = event.payload.get("regime")
        conf = event.payload.get("regime_confidence")
        if label is not None:
            self._regime = (label, float(conf or 0.0))

    # -- pnl accounting -------------------------------------------------------
    def _apply_pnl(self, entry: MatchedOrderLog) -> None:
        sym = entry.asset.upper()
        pos = self._positions.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        signed = entry.filled_volume * (1 if entry.side.value.startswith("LONG") else -1)
        is_close = entry.side.value.endswith("CLOSE")
        if is_close and pos["qty"] != 0.0:
            direction = 1.0 if pos["qty"] > 0 else -1.0
            realized = direction * (entry.fill_price - pos["avg"]) * entry.filled_volume
            realized -= entry.commission
            self._realized_session += realized
            self._realized_total += realized
            if realized >= 0:
                self._wins += 1
                self._gross_profit += realized
            else:
                self._losses += 1
                self._gross_loss += abs(realized)
            pos["qty"] -= direction * entry.filled_volume
        else:
            new_qty = pos["qty"] + signed
            if new_qty != 0:
                pos["avg"] = (
                    pos["avg"] * abs(pos["qty"]) + entry.fill_price * abs(signed)
                ) / abs(new_qty)
            pos["qty"] = new_qty
        self._marks[sym] = entry.fill_price
        self._recompute_unrealized()
        self._update_equity()

    def _recompute_unrealized(self) -> None:
        total = 0.0
        for sym, pos in self._positions.items():
            mark = self._marks.get(sym, pos["avg"])
            total += (mark - pos["avg"]) * pos["qty"]
        self._unrealized = total

    def _update_equity(self) -> None:
        equity = self._starting_capital + self._realized_total + self._unrealized
        prev = self._equity[-1] if self._equity else equity
        self._equity.append(equity)
        if prev:
            self._returns.append((equity - prev) / prev)
        self._peak_equity = max(self._peak_equity, equity)
        if self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity
            self._max_drawdown = max(self._max_drawdown, dd)

    # -- derived metrics ------------------------------------------------------
    def _pnl_hud(self) -> PnLTelemetryHUD:
        trades = self._wins + self._losses
        win_rate = self._wins / trades if trades else 0.0
        profit_factor = (
            self._gross_profit / self._gross_loss if self._gross_loss > 0 else 0.0
        )
        return PnLTelemetryHUD(
            realizedPnlSession=round(self._realized_session, 2),
            realizedPnlTotal=round(self._realized_total, 2),
            unrealizedPnl=round(self._unrealized, 2),
            winRate=round(win_rate, 4),
            profitFactor=round(profit_factor, 3),
            maxDrawdownPct=round(self._max_drawdown, 4),
            sharpeRatio=round(self._sharpe(), 3),
            sortinoRatio=round(self._sortino(), 3),
            equityCurve=[round(x, 2) for x in self._equity],
        )

    def _sharpe(self) -> float:
        rets = list(self._returns)
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(252)

    def _sortino(self) -> float:
        rets = list(self._returns)
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        downside = [r for r in rets if r < 0]
        if not downside:
            return 0.0
        dd = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
        if dd == 0:
            return 0.0
        return (mean / dd) * math.sqrt(252)

    def _margin_matrix(self) -> MarginSecurityMatrix:
        gross_notional = sum(
            abs(pos["qty"]) * self._marks.get(sym, pos["avg"])
            for sym, pos in self._positions.items()
        )
        equity = self._equity[-1] if self._equity else self._starting_capital
        maintenance = gross_notional * 0.005
        free_margin = max(0.0, equity - maintenance)
        leverage = gross_notional / equity if equity > 0 else 0.0
        liq_distance = 1.0 if gross_notional == 0 else max(
            0.0, min(1.0, free_margin / max(1.0, maintenance * 4.0))
        )
        return MarginSecurityMatrix(
            startingCapital=round(self._starting_capital, 2),
            portfolioEquity=round(equity, 2),
            freeMargin=round(free_margin, 2),
            maintenanceMargin=round(maintenance, 2),
            leverageExposureRatio=round(leverage, 3),
            liquidationDistance=round(liq_distance, 4),
            grossNotional=round(gross_notional, 2),
        )

    def _allocation_radar(self) -> AssetAllocationRadar:
        equity = self._equity[-1] if self._equity else self._starting_capital
        cells: list[AllocationCell] = []
        desk_weights: dict[str, float] = {d.value: 0.0 for d in DeskTier}
        for sym, pos in self._positions.items():
            if pos["qty"] == 0:
                continue
            try:
                desk = AssetUniverse.desk_of(sym)
            except KeyError:
                continue
            mark = self._marks.get(sym, pos["avg"])
            notional = abs(pos["qty"]) * mark
            weight = notional / equity if equity > 0 else 0.0
            desk_weights[desk.value] += weight
            cells.append(AllocationCell(
                asset=sym,
                desk=DeskTier(desk.value),
                weight=round(min(1.0, weight), 4),
                directionalBias=1.0 if pos["qty"] > 0 else -1.0,
                markPrice=round(mark, 8),
            ))
        return AssetAllocationRadar(
            mode=get_mode_manager().mode.value,
            deskWeights={k: round(v, 4) for k, v in desk_weights.items()},
            cells=cells,
        )

    # -- frame ----------------------------------------------------------------
    def frame(self) -> CockpitTelemetryFrame:
        return CockpitTelemetryFrame(
            ts=datetime.now(timezone.utc).isoformat(),
            mode=get_mode_manager().mode.value,
            flightLog=list(self._ledger),
            pnlHud=self._pnl_hud(),
            marginMatrix=self._margin_matrix(),
            allocationRadar=self._allocation_radar(),
            macroStrip=MacroContextStrip(
                regimeLabel=self._regime[0],
                regimeConfidence=self._regime[1],
                fedFundsRate=self._macro["fed_funds"],
                dollarIndex=self._macro["dxy"],
                goldSpot=self._macro["gold"],
                simDay=self._sim_day,
            ),
        )

    def snapshot(self) -> dict:
        return self.frame().model_dump(by_alias=True)


def build_cockpit_router(hub: CockpitTelemetryHub, prefix: str = "/api/v1") -> APIRouter:
    """FastAPI router exposing the cockpit REST snapshot + streaming socket."""
    router = APIRouter()

    @router.get(f"{prefix}/cockpit/snapshot")
    async def cockpit_snapshot() -> dict:
        return hub.snapshot()

    @router.websocket(f"{prefix}/stream/cockpit")
    async def cockpit_stream(ws: WebSocket) -> None:
        await ws.accept()
        try:
            while True:
                await ws.send_json({"type": "frame", "frame": hub.snapshot()})
                await asyncio.sleep(0.1)  # 10 Hz cockpit refresh
        except WebSocketDisconnect:
            return
        except Exception:  # noqa: BLE001 - never let the socket loop crash the app
            logger.exception("cockpit websocket error")
            await ws.close()

    return router
