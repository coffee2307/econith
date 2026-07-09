"use client";

import { useEffect, useRef, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faBell, faLayerGroup } from "@fortawesome/free-solid-svg-icons";
import type { SimEvent } from "@/lib/worldModel";
import { useLocale } from "@/contexts/LocaleContext";

const MAX_PENDING_BADGE = 8;

const LEVEL_TEXT: Record<string, string> = {
  info: "text-muted",
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
};
const LEVEL_DOT: Record<string, string> = {
  info: "bg-faint",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
};

export function EventLogQueue({
  events,
  pendingCount,
  embedded = false,
}: {
  events: SimEvent[];
  pendingCount: number;
  embedded?: boolean;
}) {
  const { t } = useLocale();
  const badge =
    pendingCount > MAX_PENDING_BADGE
      ? `+${MAX_PENDING_BADGE}`
      : pendingCount > 0
        ? `+${pendingCount}`
        : null;

  return (
    <div className={embedded ? "flex min-h-0 flex-1 flex-col overflow-hidden" : "flex min-h-0 min-w-0 flex-col overflow-hidden border-l border-line bg-surface"}>
      {!embedded ? (
      <div className="flex flex-none items-center gap-2 border-b border-line px-4 py-3">
        <FontAwesomeIcon icon={faBell} className="h-4 w-4 text-world" />
        <h2 className="text-sm font-bold">{t("world.globalEvents")}</h2>
        {badge ? (
          <span className="ml-auto inline-flex items-center gap-1 rounded-full border border-line bg-elevated px-2 py-0.5 font-mono text-[10px] text-muted">
            <FontAwesomeIcon icon={faLayerGroup} className="h-2.5 w-2.5" />
            {badge} {t("world.queued")}
          </span>
        ) : null}
      </div>
      ) : badge ? (
        <div className="flex flex-none items-center justify-end border-b border-line px-4 py-1.5">
          <span className="inline-flex items-center gap-1 font-mono text-[10px] text-muted">
            <FontAwesomeIcon icon={faLayerGroup} className="h-2.5 w-2.5" />
            {badge} {t("world.queued")}
          </span>
        </div>
      ) : null}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4">
        <div className="space-y-2">
          {events.length === 0 ? (
            <p className="font-mono text-xs text-faint">
              {t("world.waitingEvents")}
            </p>
          ) : (
            events.map((e) => <QueuedRow key={e.id} e={e} />)
          )}
        </div>
      </div>
    </div>
  );
}

function QueuedRow({ e }: { e: SimEvent }) {
  const { simEventMessage, simEventSource, locale } = useLocale();
  const [entered, setEntered] = useState(false);
  const raf = useRef<number | null>(null);

  useEffect(() => {
    raf.current = requestAnimationFrame(() => setEntered(true));
    return () => {
      if (raf.current !== null) cancelAnimationFrame(raf.current);
    };
  }, []);

  const timeLocale = locale === "vi" ? "vi-VN" : "en-GB";

  return (
    <div
      className={[
        "rounded-xl border border-line bg-elevated px-3 py-2 transition-all duration-500 ease-out",
        entered ? "translate-y-0 opacity-100" : "-translate-y-1 opacity-0",
      ].join(" ")}
    >
      <div className="flex items-center justify-between text-[10px] uppercase tracking-wider">
        <span className={`flex items-center gap-1.5 ${LEVEL_TEXT[e.level] ?? "text-muted"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${LEVEL_DOT[e.level] ?? "bg-faint"}`} />
          {simEventSource(e.source)}
        </span>
        <span className="font-mono text-faint">
          {new Date(e.ts).toLocaleTimeString(timeLocale, { hour12: false })}
        </span>
      </div>
      <p className="mt-1 text-xs text-ink">{simEventMessage(e)}</p>
    </div>
  );
}
