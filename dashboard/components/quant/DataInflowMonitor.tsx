"use client";

/**
 * ECONITH :: DataInflowMonitor  (RISK/DATA rail)
 */
import { useEffect, useState } from "react";
import { faDatabase } from "@fortawesome/free-solid-svg-icons";
import { API_BASE } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import type { MacroVector, WorldGlobalMacro } from "@/hooks/useMetricsStream";
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

export function DataInflowMonitor({ macro }: { macro?: WorldGlobalMacro | MacroVector }) {
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

  const macroEntries = macro ? Object.entries(macro).slice(0, 6) : [];
  const vendorEntries = Object.entries(vendors);
  const readyCount = vendorEntries.filter(([, s]) => s.status === "READY").length;
  const total = vendorEntries.length;

  return (
    <Panel
      title={t("quant.dataInflow.title")}
      icon={faDatabase}
      zone="risk"
      bodyClassName="flex flex-col gap-2"
      right={
        <span className="wr-panel-eyebrow">
          {total
            ? t("quant.dataInflow.online", { ready: readyCount, total })
            : "—"}
        </span>
      }
    >
      <div className="grid grid-cols-2 gap-1.5">
        {macroEntries.length ? (
          macroEntries.map(([k, v]) => (
            <div key={k} className="rounded-md border border-line bg-base/40 px-2 py-1">
              <p className="truncate text-[8px] uppercase tracking-wider text-faint">{k}</p>
              <p className="font-mono text-[11px] text-ink">{fmtNum(Number(v), 4)}</p>
            </div>
          ))
        ) : (
          <p className="col-span-2 text-[11px] text-faint">{t("quant.dataInflow.awaitingMacro")}</p>
        )}
      </div>

      <div>
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
