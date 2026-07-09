"use client";

import { useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faBell, faComments } from "@fortawesome/free-solid-svg-icons";
import { EventLogQueue } from "@/components/EventLogQueue";
import { WorldAgentExchange } from "@/components/world/WorldAgentExchange";
import { useLocale } from "@/contexts/LocaleContext";
import type { SimEvent } from "@/lib/worldModel";

type PanelTab = "events" | "agents";

export function WorldRightPanel({
  events,
  pendingCount,
}: {
  events: SimEvent[];
  pendingCount: number;
}) {
  const { t } = useLocale();
  const [tab, setTab] = useState<PanelTab>("events");

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
