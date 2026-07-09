"use client";

/**
 * ECONITH :: EventStreamCard
 *
 * Card representation of a single telemetry event. Replaces raw log lines with
 * a level rail + icon + source chip so operators parse severity at a glance.
 */
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faCircleInfo,
  faCircleCheck,
  faTriangleExclamation,
  faCircleXmark,
} from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import type { LogEvent } from "@/hooks/useMetricsStream";
import { fmtClock } from "@/lib/format";

const LEVEL: Record<string, { color: string; icon: IconDefinition }> = {
  info: { color: "var(--color-muted)", icon: faCircleInfo },
  ok: { color: "var(--wr-long)", icon: faCircleCheck },
  warn: { color: "#f59e0b", icon: faTriangleExclamation },
  danger: { color: "var(--wr-short)", icon: faCircleXmark },
};

export function EventStreamCard({ event }: { event: LogEvent }) {
  const lv = LEVEL[event.level] ?? LEVEL.info;

  return (
    <div
      className="relative flex items-start gap-2 rounded-md border border-line bg-base/50 py-1.5 pl-3 pr-2"
      style={{ boxShadow: `inset 2px 0 0 0 ${lv.color}` }}
    >
      <FontAwesomeIcon
        icon={lv.icon}
        className="mt-0.5 h-3 w-3 shrink-0"
        style={{ color: lv.color }}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="truncate font-mono text-[10px] font-semibold uppercase tracking-wide text-faint">
            {event.source}
          </span>
          <span className="shrink-0 font-mono text-[9px] text-faint">{fmtClock(event.ts)}</span>
        </div>
        <p className="mt-0.5 truncate text-[11px] leading-snug text-ink">{event.message}</p>
      </div>
    </div>
  );
}
