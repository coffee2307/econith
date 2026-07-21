"use client";

/**
 * Thin journalist news ticker — surfaces recent journalist/world Event Log lines.
 */
import { useMemo } from "react";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";

export function JournalistTicker() {
  const { snapshot } = useMetrics();
  const { locale } = useLocale();
  const vi = locale === "vi";

  const lines = useMemo(() => {
    const events = snapshot?.world_events ?? [];
    return events
      .filter((e) => {
        const src = (e.source || "").toLowerCase();
        return src === "journalist" || src === "hypothesis" || src === "regime";
      })
      .slice(0, 8);
  }, [snapshot?.world_events]);

  if (!lines.length) {
    return (
      <div className="rounded-xl border border-line bg-surface px-3 py-2">
        <p className="text-[10px] font-semibold uppercase tracking-wider text-faint">
          {vi ? "Băng tin" : "News ticker"}
        </p>
        <p className="mt-1 font-mono text-[11px] text-muted">
          {vi ? "đang chờ journalist / hypothesis / regime…" : "waiting for journalist / hypothesis / regime…"}
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-line bg-surface px-3 py-2">
      <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-faint">
        {vi ? "Băng tin" : "News ticker"}
      </p>
      <ul className="space-y-1">
        {lines.map((e, i) => (
          <li
            key={`${e.ts}-${i}`}
            className="flex gap-2 border-b border-line/60 py-1 last:border-0"
          >
            <span className="shrink-0 font-mono text-[9px] uppercase text-faint">
              {e.source}
            </span>
            <span className="min-w-0 truncate text-[11px] text-ink">
              {String(e.message || "").slice(0, 160)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
