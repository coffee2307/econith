/**
 * ECONITH :: dashboard/lib/cockpit/types.ts
 *
 * Aviation-cockpit telemetry contracts for the Next-Gen Quant Cockpit.
 *
 * These interfaces are the single source of truth shared between the FastAPI
 * WebSocket backend (`core/cockpit/schemas.py`, Pydantic V2) and the React 19
 * concurrent-mode cockpit widgets. Every widget consumes a strongly-typed
 * slice of `ICockpitTelemetryFrame` streamed over the non-blocking EventBus
 * socket at high frequency.
 */

/** Position-lifecycle-aware execution side. */
export type ExecutionSide =
  | "LONG_OPEN"
  | "LONG_CLOSE"
  | "SHORT_OPEN"
  | "SHORT_CLOSE";

/** Order execution type. */
export type ExecutionType = "MARKET" | "LIMIT";

/** Sovereign operating mode. */
export type QuantMode = "REALITY" | "SIMULATION";

/** Desk tier for the radar allocation container. */
export type DeskTier =
  | "crypto_majors"
  | "crypto_high_beta"
  | "crypto_meme"
  | "tradfi_forex"
  | "commodities"
  | "sovereign";

/**
 * Widget 1 — Matched Order Execution Ledger (The Flight Log).
 * One streamed executed transaction packet from the CCXT Binance bridge.
 */
export interface IMatchedOrderLog {
  /** Unique exchange order id. */
  orderId: string;
  /** Deterministic client-assigned id (idempotency key). */
  clientOrderId: string;
  /** Exact fill time, microsecond resolution. */
  timestampUs: number;
  /** Target asset identity (coin name / symbol). */
  asset: string;
  /** Execution side. */
  side: ExecutionSide;
  /** Execution type. */
  executionType: ExecutionType;
  /** Matched filled volume (base units). */
  filledVolume: number;
  /** Exact fill price. */
  fillPrice: number;
  /** Slippage delta vs reference/mark price. */
  slippageDelta: number;
  /** Paid commission / taker fees (quote units). */
  commission: number;
  /** Sovereign mode this fill was routed under. */
  mode: QuantMode;
}

/**
 * Widget 2 — Hyper-Detailed Performance & PnL HUD (The Altimeter).
 */
export interface IPnLTelemetryHUD {
  /** Realized PnL for the active session. */
  realizedPnlSession: number;
  /** Realized PnL cumulative (all sessions). */
  realizedPnlTotal: number;
  /** Floating / unrealized PnL vs live mark prices. */
  unrealizedPnl: number;
  /** Global trade win rate in [0, 1]. */
  winRate: number;
  /** Profit factor (gross profit / gross loss). */
  profitFactor: number;
  /** Maximum peak-to-trough drawdown fraction in [0, 1]. */
  maxDrawdownPct: number;
  /** Annualized Sharpe ratio. */
  sharpeRatio: number;
  /** Sortino (downside-only) tail-risk metric. */
  sortinoRatio: number;
  /** Rolling equity curve samples for the altimeter trace. */
  equityCurve: number[];
}

/**
 * Widget 3 — Capital Base & Margin Security Matrix (The Fuel Gauge).
 */
export interface IMarginSecurityMatrix {
  /** Total starting capital. */
  startingCapital: number;
  /** Floating portfolio equity (capital + unrealized). */
  portfolioEquity: number;
  /** Net free available margin. */
  freeMargin: number;
  /** Maintenance margin requirement. */
  maintenanceMargin: number;
  /** Leveraged margin exposure ratio (notional / equity). */
  leverageExposureRatio: number;
  /** Distance to liquidation as a fraction in [0, 1]; 1 = safe. */
  liquidationDistance: number;
  /** Aggregate open notional exposure. */
  grossNotional: number;
}

/** A single desk/asset weight cell in the radar container. */
export interface IAllocationCell {
  asset: string;
  desk: DeskTier;
  /** Allocated capital weight in [0, 1]. */
  weight: number;
  /** Signed current directional exposure in [-1, 1]. */
  directionalBias: number;
  /** Current mark price, if tracked. */
  markPrice: number | null;
}

/**
 * Widget 4 — Target Asset Allocation Matrix (The Radar).
 */
export interface IAssetAllocationRadar {
  mode: QuantMode;
  /** Per-desk aggregate weights. */
  deskWeights: Record<DeskTier, number>;
  /** Individual asset cells. */
  cells: IAllocationCell[];
}

/** Macro spine + regime readout mirrored from the CORE for the HUD chrome. */
export interface IMacroContextStrip {
  regimeLabel: string;
  regimeConfidence: number;
  fedFundsRate: number | null;
  dollarIndex: number | null;
  goldSpot: number | null;
  simDay: number;
}

/**
 * The unified cockpit telemetry frame streamed on every push.
 */
export interface ICockpitTelemetryFrame {
  /** ISO-8601 emission timestamp. */
  ts: string;
  mode: QuantMode;
  flightLog: IMatchedOrderLog[];
  pnlHud: IPnLTelemetryHUD;
  marginMatrix: IMarginSecurityMatrix;
  allocationRadar: IAssetAllocationRadar;
  macroStrip: IMacroContextStrip;
}

/** Discriminated envelope for every message on the cockpit socket. */
export type CockpitSocketMessage =
  | { type: "frame"; frame: ICockpitTelemetryFrame }
  | { type: "fill"; fill: IMatchedOrderLog }
  | { type: "news"; line: ICockpitNewsLine }
  | { type: "heartbeat"; ts: string };

/** A Journalist-LLM synthesized news line for the cockpit ticker. */
export interface ICockpitNewsLine {
  ts: string;
  category: string;
  level: "info" | "ok" | "warn" | "danger";
  message: string;
}
