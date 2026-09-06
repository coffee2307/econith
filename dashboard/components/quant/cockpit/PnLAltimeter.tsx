"use client";

import type { IPnLTelemetryHUD } from "@/lib/cockpit/types";
import { fmtNum, fmtPct, fmtSigned } from "@/lib/format";

function EquitySparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const w = 120;
  const h = 36;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      return `${x},${y}`;
    })
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-9 w-28 shrink-0" aria-hidden>
      <polyline
        fill="none"
        stroke={up ? "#22c55e" : "#ef4444"}
        strokeWidth="1.5"
        points={pts}
      />
    </svg>
  );
}

export function PnLAltimeter({
  title,
  hud,
  labels,
}: {
  title: string;
  hud: IPnLTelemetryHUD;
  labels: {
    realized: string;
    unrealized: string;
    winRate: string;
    sharpe: string;
    drawdown: string;
  };
}) {
  const pnlUp = hud.unrealizedPnl >= 0;
  return (
    <section className="quant-frame flex h-full flex-col p-3">
      <header className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          {title}
        </h3>
        <EquitySparkline values={hud.equityCurve} />
      </header>
      <div className="grid flex-1 grid-cols-2 gap-2 sm:grid-cols-3">
        <Readout
          label={labels.realized}
          value={fmtSigned(hud.realizedPnlSession, 2)}
          prefix="$"
          accent={hud.realizedPnlSession >= 0 ? "text-ok" : "text-danger"}
        />
        <Readout
          label={labels.unrealized}
          value={fmtSigned(hud.unrealizedPnl, 2)}
          prefix="$"
          accent={pnlUp ? "text-ok" : "text-danger"}
        />
        <Readout label={labels.winRate} value={fmtPct(hud.winRate, 1)} />
        <Readout label={labels.sharpe} value={fmtNum(hud.sharpeRatio, 2)} />
        <Readout
          label={labels.drawdown}
          value={fmtPct(hud.maxDrawdownPct, 2)}
          accent={hud.maxDrawdownPct > 0.05 ? "text-danger" : undefined}
        />
        <Readout label="PF" value={fmtNum(hud.profitFactor, 2)} />
      </div>
    </section>
  );
}

function Readout({
  label,
  value,
  prefix,
  accent = "text-ink",
}: {
  label: string;
  value: string;
  prefix?: string;
  accent?: string;
}) {
  return (
    <div className="quant-readout">
      <p className="quant-readout-label">{label}</p>
      <p className={`quant-readout-value text-base ${accent}`}>
        {prefix}
        {value}
      </p>
    </div>
  );
}