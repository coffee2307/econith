"use client";

import { useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faBell, faComments } from "@fortawesome/free-solid-svg-icons";
import { EventLogQueue } from "@/components/EventLogQueue";
import { WorldAgentExchange } from "@/components/world/WorldAgentExchange";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";
import type { SimEvent } from "@/lib/worldModel";

type PanelTab = "events" | "agents";

export function WorldRightPanel({
  events,
  pendingCount,
  running,
  simDay,
}: {
  events: SimEvent[];
  pendingCount: number;
  running: boolean;
  simDay: number;
}) {
  const { t, locale } = useLocale();
  const { snapshot } = useMetrics();
  const [tab, setTab] = useState<PanelTab>("events");
  const global = snapshot?.world?.global;

  return (
    <aside className="flex min-h-0 min-w-0 flex-col overflow-hidden border-l border-line bg-surface">
      <div className="flex flex-none border-b border-line">
        <TabButton
          active={tab === "events"}
          onClick={() => setTab("events")}
          icon={faBell}
          label={t("world.rightPanelEvents")}
          badge={
            pendingCount > 0
              ? `+${pendingCount > 8 ? 8 : pendingCount}`
              : undefined
          }
        />
        <TabButton
          active={tab === "agents"}
          onClick={() => setTab("agents")}
          icon={faComments}
          label={t("world.rightPanelAgents")}
        />
      </div>
      <p className="flex-none border-b border-line px-3 py-1.5 text-[10px] leading-snug text-muted">
        {tab === "events" ? t("world.eventsPanelHint") : t("world.agentsPanelHint")}
      </p>
      <div className={`flex flex-none items-center gap-2 border-b border-line px-3 py-2 ${running ? "bg-ok/5" : "bg-elevated/50"}`}>
        <span className={`h-2 w-2 rounded-full ${running ? "animate-pulse bg-ok" : "bg-faint"}`} />
        <div className="min-w-0">
          <p className={`text-[10px] font-semibold ${running ? "text-ok" : "text-muted"}`}>
            {running
              ? locale === "vi" ? "World đang xử lý từng ngày mô phỏng" : "World is processing each simulated day"
              : locale === "vi" ? "World đang dừng — bấm Chạy để bắt đầu" : "World is paused — press Play to begin"}
          </p>
          <p className="mt-0.5 truncate font-mono text-[9px] text-faint">
            {locale === "vi" ? "Ngày" : "Day"} {simDay.toLocaleString()}
            {running && events.length === 0
              ? locale === "vi" ? " · trạng thái ổn định, chưa có sự kiện vượt ngưỡng" : " · stable state, no threshold event"
              : ""}
          </p>
        </div>
      </div>
      {running && global ? (
        <div className="grid flex-none grid-cols-2 gap-px border-b border-line bg-line">
          <LiveMetric label={locale === "vi" ? "Tăng trưởng" : "Growth"} value={global.gdp_growth} />
          <LiveMetric label={locale === "vi" ? "Lạm phát" : "Inflation"} value={global.inflation} />
          <LiveMetric label={locale === "vi" ? "Lãi suất" : "Interest"} value={global.interest_rate} />
          <LiveMetric label={locale === "vi" ? "Căng thẳng" : "Tension"} value={global.trade_tension ?? 0} />
        </div>
      ) : null}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {tab === "events" ? (
          <EventLogQueue events={events} pendingCount={pendingCount} embedded />
        ) : (
          <WorldAgentExchange />
        )}
      </div>
    </aside>
  );
}

function LiveMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-surface px-3 py-2">
      <p className="text-[8px] uppercase tracking-wide text-faint">{label}</p>
      <p className="mt-0.5 font-mono text-[11px] font-semibold text-ink">{(value * 100).toFixed(2)}%</p>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  icon: typeof faBell;
  label: string;
  badge?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "flex flex-1 items-center justify-center gap-1.5 px-3 py-2.5 text-xs font-semibold transition-colors",
        active
          ? "border-b-2 border-world bg-elevated text-world"
          : "text-muted hover:bg-elevated hover:text-ink",
      ].join(" ")}
    >
      <FontAwesomeIcon icon={icon} className="h-3.5 w-3.5" />
      {label}
      {badge ? (
        <span className="rounded-full bg-elevated px-1.5 font-mono text-[9px] text-warn">
          {badge}
        </span>
      ) : null}
    </button>
  );
}
