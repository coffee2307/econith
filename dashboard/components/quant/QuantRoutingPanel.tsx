"use client";

/**
 * ECONITH :: QuantRoutingPanel  (EXECUTION zone)
 */
import { useEffect, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faDiagramProject, faLock } from "@fortawesome/free-solid-svg-icons";
import { API_BASE } from "@/lib/api";
import { fmtNum, fmtPct } from "@/lib/format";
import type { RoutingState } from "@/hooks/useMetricsStream";
import { Panel } from "@/components/quant/ui/Panel";
import { useLocale } from "@/contexts/LocaleContext";
import { tQuantEnum } from "@/lib/i18n/quantEnum";

interface RouterProfilePayload {
  name: string;
  symbols: string[];
  max_leg_fraction: number;
  bias_multiplier: number;
}

interface RoutingStatus {
  active_profile?: RouterProfilePayload;
  available_profiles?: Record<string, RouterProfilePayload>;
}

const PROFILES = ["balanced", "aggressive", "defensive"] as const;

export function QuantRoutingPanel({
  routing,
  className = "",
}: {
  routing?: RoutingState;
  className?: string;
}) {
  const { t } = useLocale();
  const [status, setStatus] = useState<RoutingStatus | null>(null);
  const [err, setErr] = useState("");

  const load = async (signal?: AbortSignal) => {
    try {
      const res = await fetch(`${API_BASE}/quant/routing/status`, { signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setStatus(await res.json());
      setErr("");
    } catch (e) {
      if ((e as Error).name !== "AbortError") setErr((e as Error).message);
    }
  };

  useEffect(() => {
    const c = new AbortController();
    void load(c.signal);
    const id = setInterval(() => load(c.signal), 3000);
    return () => {
      c.abort();
      clearInterval(id);
    };
  }, []);

  const changeProfile = async (profile: string) => {
    try {
      await fetch(`${API_BASE}/quant/routing/profile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile }),
      });
    } finally {
      await load();
    }
  };

  const active = status?.active_profile;
  const maxLeg = active?.max_leg_fraction ?? 0;
  const legs = routing?.legs ?? [];
  const profileKey = routing?.profile ?? active?.name;
  const profileLabel = profileKey
    ? t(`quant.routing.profiles.${profileKey}`) !== `quant.routing.profiles.${profileKey}`
      ? t(`quant.routing.profiles.${profileKey}`)
      : profileKey
    : "—";

  return (
    <Panel
      title={t("quant.routing.title")}
      icon={faDiagramProject}
      zone="exec"
      className={className}
      bodyClassName="flex flex-col gap-2.5"
      right={<span className="wr-panel-eyebrow">{profileLabel}</span>}
    >
      <div className="flex flex-wrap gap-1">
        {PROFILES.map((p) => {
          const on = active?.name === p;
          return (
            <button
              key={p}
              type="button"
              onClick={() => changeProfile(p)}
              className={[
                "rounded-md border px-2 py-1 font-mono text-[9px] font-bold uppercase tracking-wider transition-colors",
                on
                  ? "border-zone-exec bg-zone-exec/15 text-zone-exec"
                  : "border-line text-faint hover:text-ink",
              ].join(" ")}
            >
              {t(`quant.routing.profiles.${p}`)}
            </button>
          );
        })}
      </div>

      <div
        className={`h-4 shrink-0 truncate text-[10px] leading-4 ${err ? "text-danger" : "invisible"}`}
        aria-live="polite"
      >
        {err ? `${t("quant.routing.errorPrefix")} ${err}` : "\u00a0"}
      </div>

      <div className="grid shrink-0 grid-cols-4 gap-1.5">
        <Stat label={t("quant.routing.maxLeg")} value={fmtPct(maxLeg, 0)} />
        <Stat label={t("quant.routing.biasMult")} value={fmtNum(active?.bias_multiplier ?? 1, 2)} />
        <Stat
          label={t("quant.routing.conf")}
          value={routing?.confidence != null ? fmtPct(routing.confidence, 0) : "—"}
        />
        <Stat label={t("quant.routing.legs")} value={String(legs.length)} />
      </div>

      <div
        className={[
          "flex h-[26px] shrink-0 items-center gap-1.5 rounded-md border px-2 text-[10px] font-semibold",
          routing?.reduce_only
            ? "border-warn/40 bg-warn/10 text-warn"
            : "border-transparent text-transparent select-none",
        ].join(" ")}
        aria-hidden={!routing?.reduce_only}
      >
        <FontAwesomeIcon icon={faLock} className="h-2.5 w-2.5 shrink-0" aria-hidden />
        <span>{t("quant.routing.reduceOnly")}</span>
      </div>

      <div className="shrink-0 rounded-md border border-line bg-base/40">
        {legs.length ? (
          <table className="w-full font-mono text-[10px]">
            <thead>
              <tr className="border-b border-line text-left text-faint">
                <th className="px-2 py-1.5 font-semibold uppercase tracking-wider">
                  {t("quant.routing.cols.symbol")}
                </th>
                <th className="px-2 py-1.5 font-semibold uppercase tracking-wider">
                  {t("quant.routing.cols.side")}
                </th>
                <th className="px-2 py-1.5 font-semibold uppercase tracking-wider">
                  {t("quant.routing.cols.qty")}
                </th>
                <th className="px-2 py-1.5 font-semibold uppercase tracking-wider">
                  {t("quant.routing.cols.wt")}
                </th>
                <th className="hidden px-2 py-1.5 font-semibold uppercase tracking-wider sm:table-cell">
                  {t("quant.routing.cols.desk")}
                </th>
              </tr>
            </thead>
            <tbody>
              {legs.map((leg, i) => (
                <tr key={`${leg.symbol}-${i}`} className="border-b border-line/50 last:border-0">
                  <td className="px-2 py-1.5 text-ink">{leg.symbol}</td>
                  <td className={`px-2 py-1.5 font-semibold ${leg.side === "BUY" ? "text-long" : "text-short"}`}>
                    {tQuantEnum(t, "side", leg.side)}
                  </td>
                  <td className="px-2 py-1.5 text-muted">{fmtNum(leg.quantity, 5)}</td>
                  <td className="px-2 py-1.5">
                    <div className="flex items-center gap-1.5">
                      <div className="h-1 w-10 overflow-hidden rounded-full bg-elevated">
                        <div
                          className={`h-full ${leg.side === "BUY" ? "bg-long" : "bg-short"}`}
                          style={{ width: `${Math.min(100, leg.weight * 100)}%` }}
                        />
                      </div>
                      <span className="text-faint">{fmtPct(leg.weight, 0)}</span>
                    </div>
                  </td>
                  <td className="hidden truncate px-2 py-1.5 text-faint sm:table-cell">{leg.desk}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="px-2 py-3 text-center text-[11px] text-faint">{t("quant.routing.awaitingPlan")}</p>
        )}
      </div>
    </Panel>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-elevated/60 px-1.5 py-1 text-center">
      <p className="wr-label">{label}</p>
      <p className="wr-value text-[13px]">{value}</p>
    </div>
  );
}
