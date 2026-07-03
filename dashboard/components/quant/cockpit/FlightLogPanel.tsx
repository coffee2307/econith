"use client";

import type { IMatchedOrderLog } from "@/lib/cockpit/types";
import { fmtNum, fmtUsd } from "@/lib/format";

const SIDE_COLOR: Record<string, string> = {
  LONG_OPEN: "text-ok",
  LONG_CLOSE: "text-ok/70",
  SHORT_OPEN: "text-danger",
  SHORT_CLOSE: "text-danger/70",
};

function formatTs(us: number): string {
  const d = new Date(us / 1000);
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  } as Intl.DateTimeFormatOptions);
}

export function FlightLogPanel({
  title,
  empty,
  entries,
}: {
  title: string;
  empty: string;
  entries: IMatchedOrderLog[];
}) {
  return (
    <section className="quant-frame flex h-full min-h-0 flex-col overflow-hidden">
      <header className="shrink-0 border-b border-line px-3 py-2">
        <h3 className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-faint">
          {title}
        </h3>
      </header>
      <div className="min-h-0 flex-1 overflow-auto font-mono text-[10px]">
        {entries.length === 0 ? (
          <p className="p-4 text-center text-muted">{empty}</p>
        ) : (
          <table className="w-full border-collapse">
            <thead className="sticky top-0 bg-surface text-[9px] uppercase tracking-wider text-faint">
              <tr>
                <th className="px-2 py-1 text-left">Time</th>
                <th className="px-2 py-1 text-left">Asset</th>
                <th className="px-2 py-1 text-left">Side</th>
                <th className="px-2 py-1 text-right">Qty</th>
                <th className="px-2 py-1 text-right">Price</th>
                <th className="px-2 py-1 text-right">Slip</th>
                <th className="px-2 py-1 text-right">Fee</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr
                  key={`${e.orderId}-${e.timestampUs}`}
                  className="border-t border-line/60 transition-colors hover:bg-elevated/80"
                >
                  <td className="whitespace-nowrap px-2 py-1 text-muted">
                    {formatTs(e.timestampUs)}
                  </td>
                  <td className="px-2 py-1 font-semibold text-ink">{e.asset}</td>
                  <td className={`px-2 py-1 ${SIDE_COLOR[e.side] ?? "text-ink"}`}>
                    {e.side.replace("_", " ")}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums">{fmtNum(e.filledVolume, 4)}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{fmtUsd(e.fillPrice)}</td>
                  <td className="px-2 py-1 text-right tabular-nums text-muted">
                    {fmtNum(e.slippageDelta, 4)}
                  </td>
                  <td className="px-2 py-1 text-right tabular-nums text-muted">
                    {fmtNum(e.commission, 4)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}
