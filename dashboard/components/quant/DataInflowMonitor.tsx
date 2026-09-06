"use client";

/**
 * ECONITH :: DataInflowMonitor  (RISK/DATA rail)
 */
import { useEffect, useState } from "react";
import { faDatabase } from "@fortawesome/free-solid-svg-icons";
import { API_BASE } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import type {
  AltState,
  MacroVector,
  MarketState,
  WorldGlobalMacro,
} from "@/hooks/useMetricsStream";
import { Panel } from "@/components/quant/ui/Panel";
import { useLocale } from "@/contexts/LocaleContext";
import { tQuantEnum } from "@/lib/i18n/quantEnum";

interface VendorStatus {
  status: string;
  pillar: string;
  emits: string[];
}

const STATUS_COLOR: Record<string, string> = {
  READY: "var(--wr-long)",
  MISSING: "#f59e0b",
  ERROR: "var(--wr-short)",
};

function quantTapeEntries(
  market?: MarketState,
  alt?: AltState,
): [string, number | string][] {
  const rows: [string, number | string][] = [];
  if (market?.price != null) rows.push(["PRICE", market.price]);
  if (market?.obi != null) rows.push(["OBI", market.obi]);
  if (market?.volume_delta != null) rows.push(["VOL_DELTA", market.volume_delta]);
  if (alt?.funding_rate != null) rows.push(["FUNDING", alt.funding_rate]);
  if (alt?.open_interest != null) rows.push(["OPEN_INT", alt.open_interest]);
  if (alt?.liquidation_notional != null) {
    rows.push(["LIQ_NOTIONAL", alt.liquidation_notional]);
  }
  return rows.slice(0, 6);
}

export function DataInflowMonitor({
  macro,
  market,
  alt,
  quantOnly = false,
}: {
  macro?: WorldGlobalMacro | MacroVector;
  market?: MarketState;
  alt?: AltState;
  /** When true, show live tape metrics only — no World macro. */
  quantOnly?: boolean;
}) {
  const { t } = useLocale();
  const [vendors, setVendors] = useState<Record<string, VendorStatus>>({});

  useEffect(() => {
    const c = new AbortController();
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/vendors/status`, { signal: c.signal });
        if (res.ok) setVendors(await res.json());
      } catch {
        // best-effort
      }
    };
    void load();
    const id = setInterval(load, 5000);
    return () => {
      c.abort();
      clearInterval(id);
    };
  }, []);

  const tapeEntries = quantOnly
    ? quantTapeEntries(market, alt)
    : macro
      ? Object.entries(macro).slice(0, 6)
      : [];
  const vendorEntries = Object.entries(vendors);
  const readyCount = vendorEntries.filter(([, s]) => s.status === "READY").length;
  const total = vendorEntries.length;

  const emptyLabel = quantOnly
    ? t("quant.dataInflow.awaitingTape")
    : t("quant.dataInflow.awaitingMacro");

  return (
    <Panel
      title={t("quant.dataInflow.title")}
      icon={faDatabase}
      zone="risk"
      bodyClassName="flex flex-col gap-2.5"
      className="shrink-0"
      right={
        <span className="wr-panel-eyebrow">
          {total
            ? t("quant.dataInflow.online", { ready: readyCount, total })
            : "—"}
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-1.5">
        {tapeEntries.length ? (
          tapeEntries.map(([k, v]) => (
            <div key={k} className="rounded-md border border-line bg-base/40 px-2 py-1">
              <p className="truncate text-[8px] uppercase tracking-wider text-faint">{k}</p>
              <p className="font-mono text-[11px] text-ink">
                {typeof v === "number" ? fmtNum(v, 4) : v}
              </p>
            </div>
          ))
        ) : (
          <p className="col-span-2 text-[11px] text-faint">{emptyLabel}</p>
        )}
      </div>

      <div className="max-h-36 overflow-y-auto overscroll-contain">
        <p className="wr-label mb-1">{t("quant.dataInflow.providerHealth")}</p>
        <div className="grid grid-cols-2 gap-1">
          {vendorEntries.map(([name, s]) => {
            const color = STATUS_COLOR[s.status] ?? "var(--color-muted)";
            return (
              <div
                key={name}
                className="flex items-center justify-between gap-1 rounded border border-line bg-elevated/40 px-1.5 py-1 font-mono text-[9px]"
              >
                <span className="flex items-center gap-1 truncate text-muted">
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: color, boxShadow: `0 0 5px 0 ${color}` }}
                  />
                  {name}
                </span>
                <span className="shrink-0" style={{ color }}>
                  {tQuantEnum(t, "vendorStatus", s.status)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </Panel>
  );
}
