"""ECONITH :: core.cockpit.schemas

Pydantic V2 backend contracts that mirror ``dashboard/lib/cockpit/types.ts``
1:1. The cockpit WebSocket serialises these models with ``by_alias=True`` so the
JSON on the wire matches the TypeScript camelCase interfaces exactly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ExecutionSide",
    "ExecutionType",
    "QuantModeLiteral",
    "DeskTier",
    "MatchedOrderLog",
    "PnLTelemetryHUD",
    "MarginSecurityMatrix",
    "AllocationCell",
    "AssetAllocationRadar",
    "MacroContextStrip",
    "CockpitTelemetryFrame",
    "CockpitNewsLine",
]


class ExecutionSide(str, Enum):
    LONG_OPEN = "LONG_OPEN"
    LONG_CLOSE = "LONG_CLOSE"
    SHORT_OPEN = "SHORT_OPEN"
    SHORT_CLOSE = "SHORT_CLOSE"


class ExecutionType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


QuantModeLiteral = Literal["REALITY", "SIMULATION"]


class DeskTier(str, Enum):
    CRYPTO_MAJORS = "crypto_majors"
    CRYPTO_HIGH_BETA = "crypto_high_beta"
    CRYPTO_MEME = "crypto_meme"
    TRADFI_FOREX = "tradfi_forex"
    COMMODITIES = "commodities"
    SOVEREIGN = "sovereign"


class _CamelModel(BaseModel):
    """Base emitting camelCase aliases to match the TS contracts."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class MatchedOrderLog(_CamelModel):
    """Flight-log entry (mirrors ``IMatchedOrderLog``)."""

    order_id: str = Field(alias="orderId")
    client_order_id: str = Field(alias="clientOrderId")
    timestamp_us: int = Field(alias="timestampUs")
    asset: str
    side: ExecutionSide
    execution_type: ExecutionType = Field(alias="executionType")
    filled_volume: float = Field(alias="filledVolume")
    fill_price: float = Field(alias="fillPrice")
    slippage_delta: float = Field(alias="slippageDelta")
    commission: float
    mode: QuantModeLiteral


class PnLTelemetryHUD(_CamelModel):
    """Altimeter (mirrors ``IPnLTelemetryHUD``)."""

    realized_pnl_session: float = Field(alias="realizedPnlSession", default=0.0)
    realized_pnl_total: float = Field(alias="realizedPnlTotal", default=0.0)
    unrealized_pnl: float = Field(alias="unrealizedPnl", default=0.0)
    win_rate: Annotated[float, Field(ge=0.0, le=1.0)] = Field(alias="winRate", default=0.0)
    profit_factor: float = Field(alias="profitFactor", default=0.0)
    max_drawdown_pct: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        alias="maxDrawdownPct", default=0.0
    )
    sharpe_ratio: float = Field(alias="sharpeRatio", default=0.0)
    sortino_ratio: float = Field(alias="sortinoRatio", default=0.0)
    equity_curve: list[float] = Field(alias="equityCurve", default_factory=list)


class MarginSecurityMatrix(_CamelModel):
    """Fuel gauge (mirrors ``IMarginSecurityMatrix``)."""

    starting_capital: float = Field(alias="startingCapital", default=0.0)
    portfolio_equity: float = Field(alias="portfolioEquity", default=0.0)
    free_margin: float = Field(alias="freeMargin", default=0.0)
    maintenance_margin: float = Field(alias="maintenanceMargin", default=0.0)
    leverage_exposure_ratio: float = Field(alias="leverageExposureRatio", default=0.0)
    liquidation_distance: Annotated[float, Field(ge=0.0, le=1.0)] = Field(
        alias="liquidationDistance", default=1.0
    )
    gross_notional: float = Field(alias="grossNotional", default=0.0)


class AllocationCell(_CamelModel):
    asset: str
    desk: DeskTier
    weight: Annotated[float, Field(ge=0.0, le=1.0)]
    directional_bias: Annotated[float, Field(ge=-1.0, le=1.0)] = Field(
        alias="directionalBias", default=0.0
    )
    mark_price: float | None = Field(alias="markPrice", default=None)


class AssetAllocationRadar(_CamelModel):
    """Radar (mirrors ``IAssetAllocationRadar``)."""

    mode: QuantModeLiteral
    desk_weights: dict[str, float] = Field(alias="deskWeights", default_factory=dict)
    cells: list[AllocationCell] = Field(default_factory=list)


class MacroContextStrip(_CamelModel):
    regime_label: str = Field(alias="regimeLabel", default="UNKNOWN")
    regime_confidence: float = Field(alias="regimeConfidence", default=0.0)
    fed_funds_rate: float | None = Field(alias="fedFundsRate", default=None)
    dollar_index: float | None = Field(alias="dollarIndex", default=None)
    gold_spot: float | None = Field(alias="goldSpot", default=None)
    sim_day: int = Field(alias="simDay", default=0)


class CockpitTelemetryFrame(_CamelModel):
    """The unified frame (mirrors ``ICockpitTelemetryFrame``)."""

    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    mode: QuantModeLiteral
    flight_log: list[MatchedOrderLog] = Field(alias="flightLog", default_factory=list)
    pnl_hud: PnLTelemetryHUD = Field(alias="pnlHud", default_factory=PnLTelemetryHUD)
    margin_matrix: MarginSecurityMatrix = Field(
        alias="marginMatrix", default_factory=MarginSecurityMatrix
    )
    allocation_radar: AssetAllocationRadar = Field(alias="allocationRadar")
    macro_strip: MacroContextStrip = Field(
        alias="macroStrip", default_factory=MacroContextStrip
    )


class CockpitNewsLine(_CamelModel):
    ts: str
    category: str
    level: Literal["info", "ok", "warn", "danger"] = "info"
    message: str
