"use client";

import type { IAssetAllocationRadar, DeskTier } from "@/lib/cockpit/types";
import { fmtPct } from "@/lib/format";

const DESK_LABEL: Record<DeskTier, string> = {
  crypto_majors: "Majors",
  crypto_high_beta: "High-β",
  crypto_meme: "Meme",
  tradfi_forex: "Forex",
  commodities: "Cmdty",
  sovereign: "Sov",
};

const DESK_COLOR: Record<DeskTier, string> = {
  crypto_majors: "bg-accent",
  crypto_high_beta: "bg-violet-500",
  crypto_meme: "bg-warn",
  tradfi_forex: "bg-cyan-500",
  commodities: "bg-amber-600",
  sovereign: "bg-world",
};

export function AllocationRadar({
  title,
  radar,
  modeLabel,
}: {
  title: string;
  radar: IAssetAllocationRadar;
  modeLabel: string;
}) {
  const desks = Object.entries(radar.deskWeights).filter(([, w]) => w > 0.001) as [
    DeskTier,
    number,
  ][];

  return (
    <section className="quant-frame flex h-full min-h-0 flex-col p-3">
      <header className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          {title}
        </h3>
        <span
          className={`rounded-md border px-2 py-0.5 font-mono text-[9px] font-bold uppercase ${
            radar.mode === "REALITY"
              ? "border-ok/40 text-ok"
              : "border-warn/40 text-warn"
          }`}
        >
          {modeLabel}: {radar.mode}
        </span>
      </header>

      {desks.length > 0 ? (
        <div className="mb-3 flex h-3 overflow-hidden rounded-full border border-line">
          {desks.map(([desk, weight]) => (
            <div
              key={desk}
              className={`${DESK_COLOR[desk]} transition-all duration-500`}
              style={{ width: `${weight * 100}%` }}
              title={`${DESK_LABEL[desk]} ${fmtPct(weight, 1)}`}
            />
          ))}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto font-mono text-[10px]">
        {radar.cells.length === 0 ? (
          <p className="text-center text-muted">—</p>
        ) : (
          radar.cells.map((cell) => (
            <div
              key={cell.asset}
              className="flex items-center justify-between gap-2 rounded-lg border border-line/60 bg-elevated/50 px-2 py-1.5 transition-colors hover:border-accent/30"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className={`h-2 w-2 shrink-0 rounded-full ${DESK_COLOR[cell.desk]}`}
                />
                <span className="truncate font-semibold text-ink">{cell.asset}</span>
                <span className="text-faint">{DESK_LABEL[cell.desk]}</span>
              </div>
              <div className="shrink-0 text-right">
                <span className="text-ink">{fmtPct(cell.weight, 1)}</span>
                <span
                  className={`ml-2 ${
                    cell.directionalBias > 0 ? "text-ok" : cell.directionalBias < 0 ? "text-danger" : "text-muted"
                  }`}
                >
                  {cell.directionalBias > 0 ? "L" : cell.directionalBias < 0 ? "S" : "—"}
                </span>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
