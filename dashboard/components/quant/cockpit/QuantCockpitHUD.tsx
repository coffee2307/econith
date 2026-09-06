"use client";

import { useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faGaugeHigh,
  faCircleDot,
  faChevronDown,
  faChevronUp,
} from "@fortawesome/free-solid-svg-icons";
import { useCockpitStream } from "@/hooks/useCockpitStream";
import { useLocale } from "@/contexts/LocaleContext";
import { FlightLogPanel } from "@/components/quant/cockpit/FlightLogPanel";
import { PnLAltimeter } from "@/components/quant/cockpit/PnLAltimeter";
import { MarginFuelGauge } from "@/components/quant/cockpit/MarginFuelGauge";
import { AllocationRadar } from "@/components/quant/cockpit/AllocationRadar";
import type { ICockpitTelemetryFrame } from "@/lib/cockpit/types";

const EMPTY_FRAME: ICockpitTelemetryFrame = {
  ts: "",
  mode: "REALITY",
  flightLog: [],
  pnlHud: {
    realizedPnlSession: 0,
    realizedPnlTotal: 0,
    unrealizedPnl: 0,
    winRate: 0,
    profitFactor: 0,
    maxDrawdownPct: 0,
    sharpeRatio: 0,
    sortinoRatio: 0,
    equityCurve: [],
  },
  marginMatrix: {
    startingCapital: 0,
    portfolioEquity: 0,
    freeMargin: 0,
    maintenanceMargin: 0,
    leverageExposureRatio: 0,
    liquidationDistance: 1,
    grossNotional: 0,
  },
  allocationRadar: {
    mode: "REALITY",
    deskWeights: {
      crypto_majors: 0,
      crypto_high_beta: 0,
      crypto_meme: 0,
      tradfi_forex: 0,
      commodities: 0,
      sovereign: 0,
    },
    cells: [],
  },
  macroStrip: {
    regimeLabel: "UNKNOWN",
    regimeConfidence: 0,
    fedFundsRate: null,
    dollarIndex: null,
    goldSpot: null,
    simDay: 0,
  },
};

const MAX_FLIGHT_LOG_ROWS = 6;

export function QuantCockpitHUD() {
  const { frame, status } = useCockpitStream();
  const { t } = useLocale();
  const [expanded, setExpanded] = useState(false);
  const data = frame ?? EMPTY_FRAME;
  const macro = data.macroStrip;
  const trimmedLog = data.flightLog.slice(0, MAX_FLIGHT_LOG_ROWS);

  return (
    <section className="flex min-h-0 flex-col gap-1.5">
      <header className="flex flex-wrap items-center justify-between gap-2 px-0.5">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="group flex items-center gap-2 font-mono text-xs font-bold uppercase tracking-widest text-faint transition-colors hover:text-ink"
        >
          <FontAwesomeIcon icon={faGaugeHigh} className="h-3.5 w-3.5 text-accent" />
          {t("quant.cockpit.title")}
          <FontAwesomeIcon
            icon={expanded ? faChevronUp : faChevronDown}
            className="h-2.5 w-2.5 opacity-60 transition-transform group-hover:opacity-100"
          />
        </button>
        <div className="flex flex-wrap items-center gap-2 font-mono text-[10px] text-muted">
          <span
            className={`flex items-center gap-1 ${
              status === "open" ? "text-ok" : status === "closed" ? "text-danger" : "text-warn"
            }`}
          >
            {status === "open" ? (
              <FontAwesomeIcon icon={faCircleDot} className="h-2 w-2 animate-pulse" />
            ) : null}
            {t(`quant.cockpit.ws.${status}`)}
          </span>
          <span className="text-faint">|</span>
          <span>
            {t("quant.regime")} {macro.regimeLabel} ({(macro.regimeConfidence * 100).toFixed(0)}%)
          </span>
          {data.mode === "SIMULATION" && macro.simDay > 0 ? (
            <>
              <span className="text-faint">|</span>
              <span title="Sovereign World simulation day (SIMULATION mode only)">
                {t("common.day")} {macro.simDay}
              </span>
            </>
          ) : null}
        </div>
      </header>

      <div className="quant-console grid min-h-0 grid-cols-1 gap-2 rounded-xl border border-line p-2 lg:grid-cols-2">
        <PnLAltimeter
          title={t("quant.cockpit.altimeter")}
          hud={data.pnlHud}
          labels={{
            realized: t("quant.cockpit.realized"),
            unrealized: t("quant.cockpit.unrealized"),
            winRate: t("quant.cockpit.winRate"),
            sharpe: t("quant.cockpit.sharpe"),
            drawdown: t("quant.cockpit.drawdown"),
          }}
        />
        <MarginFuelGauge
          title={t("quant.cockpit.fuelGauge")}
          matrix={data.marginMatrix}
          labels={{
            equity: t("quant.cockpit.equity"),
            freeMargin: t("quant.cockpit.freeMargin"),
            leverage: t("quant.cockpit.leverage"),
            liquidation: t("quant.cockpit.liquidation"),
            notional: t("quant.cockpit.notional"),
          }}
        />
        {expanded ? (
          <>
            <div className="h-[11rem]">
              <FlightLogPanel
                title={t("quant.cockpit.flightLog")}
                empty={t("quant.cockpit.noFills")}
                entries={trimmedLog}
              />
            </div>
            <div className="h-[11rem]">
              <AllocationRadar
                title={t("quant.cockpit.radar")}
                radar={data.allocationRadar}
                modeLabel={t("quant.cockpit.mode")}
              />
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
