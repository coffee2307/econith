"use client";

import type { IMarginSecurityMatrix } from "@/lib/cockpit/types";
import { fmtPct, fmtUsd } from "@/lib/format";

export function MarginFuelGauge({
  title,
  matrix,
  labels,
}: {
  title: string;
  matrix: IMarginSecurityMatrix;
  labels: {
    equity: string;
    freeMargin: string;
    leverage: string;
    liquidation: string;
    notional: string;
  };
}) {
  const liqPct = matrix.liquidationDistance * 100;
  const liqColor =
    liqPct > 60 ? "bg-ok" : liqPct > 30 ? "bg-warn" : "bg-danger";

  return (
    <section className="quant-frame flex h-full flex-col p-3">
      <header className="mb-2">
        <h3 className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          {title}
        </h3>
      </header>
      <div className="grid flex-1 grid-cols-2 gap-2">
        <Gauge label={labels.equity} value={`$${fmtUsd(matrix.portfolioEquity)}`} />
        <Gauge label={labels.freeMargin} value={`$${fmtUsd(matrix.freeMargin)}`} />
        <Gauge label={labels.leverage} value={`${fmtUsd(matrix.leverageExposureRatio, 2)}×`} />
        <Gauge label={labels.notional} value={`$${fmtUsd(matrix.grossNotional)}`} />
      </div>
      <div className="mt-3">
        <div className="mb-1 flex justify-between font-mono text-[9px] uppercase tracking-wider text-faint">
          <span>{labels.liquidation}</span>
          <span className={liqPct > 60 ? "text-ok" : liqPct > 30 ? "text-warn" : "text-danger"}>
            {fmtPct(matrix.liquidationDistance, 0)}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full border border-line bg-base">
          <div
            className={`h-full rounded-full transition-all duration-500 ${liqColor}`}
            style={{ width: `${Math.max(4, liqPct)}%` }}
          />
        </div>
      </div>
    </section>
  );
}

function Gauge({ label, value }: { label: string; value: string }) {
  return (
    <div className="quant-readout">
      <p className="quant-readout-label">{label}</p>
      <p className="quant-readout-value text-sm">{value}</p>
    </div>
  );
}
