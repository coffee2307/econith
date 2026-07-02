"use client";

import { useMemo, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faTerminal, faMagnifyingGlass } from "@fortawesome/free-solid-svg-icons";
import type { LogEvent } from "@/hooks/useMetricsStream";
import { fmtClock } from "@/lib/format";
import { useLocale } from "@/contexts/LocaleContext";

const LEVELS = ["all", "info", "ok", "warn", "danger"] as const;
type Level = (typeof LEVELS)[number];

const LEVEL_COLOR: Record<string, string> = {
  info: "text-muted",
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
};

export function EventLogTerminal({
  events,
  title,
  height = "h-72 max-h-72",
  fill = false,
  dock = false,
}: {
  events: LogEvent[];
  title?: string;
  height?: string;
  fill?: boolean;
  /** Full-width bottom terminal dock (Quant page). */
  dock?: boolean;
}) {
  const { t } = useLocale();
  const [level, setLevel] = useState<Level>("all");
  const [query, setQuery] = useState("");

  const resolvedTitle = title ?? t("quant.eventLogTitle");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return events.filter((e) => {
      if (level !== "all" && e.level !== level) return false;
      if (q && !e.message.toLowerCase().includes(q)) return false;
      return true;
    });
  }, [events, level, query]);

  if (dock) {
    return (
      <section className="quant-terminal-dock flex min-h-0 flex-col overflow-hidden rounded-lg border border-line bg-surface">
        <div className="flex flex-none flex-wrap items-center gap-2 border-b border-line px-3 py-2 sm:gap-3 sm:px-4">
          <div className="flex items-center gap-2">
            <FontAwesomeIcon icon={faTerminal} className="h-3.5 w-3.5 text-accent" />
            <h3 className="font-mono text-xs font-semibold uppercase tracking-wider text-ink">
              {resolvedTitle}
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            {LEVELS.map((lv) => (
              <button
                key={lv}
                type="button"
                onClick={() => setLevel(lv)}
                className={[
                  "rounded px-1.5 py-0.5 font-mono text-[10px] uppercase transition-colors",
                  level === lv
                    ? "bg-elevated text-ink"
                    : "text-faint hover:text-ink",
                ].join(" ")}
              >
                {t(`quant.levels.${lv}`)}
              </button>
            ))}
          </div>
          <div className="flex min-w-[10rem] flex-1 items-center gap-2 rounded-lg border border-line bg-base px-2 py-1 sm:min-w-[14rem]">
            <FontAwesomeIcon icon={faMagnifyingGlass} className="h-3 w-3 shrink-0 text-faint" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t("common.filterPlaceholder")}
              className="w-full min-w-0 bg-transparent font-mono text-[11px] text-ink placeholder:text-faint focus:outline-none"
            />
          </div>
        </div>
        <div className="quant-terminal-scroll min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-base px-3 py-2 font-mono text-[11px] leading-relaxed sm:px-4">
          {filtered.length === 0 ? (
            <p className="text-faint">{t("common.noEvents")}</p>
          ) : (
            <ul className="space-y-0.5">
              {filtered.map((e, i) => (
                <li
                  key={`${e.ts}-${i}`}
                  className="grid grid-cols-[4.5rem_4.5rem_4rem_minmax(0,1fr)] items-baseline gap-x-2 border-b border-line/30 py-1 last:border-0 sm:grid-cols-[5rem_5.5rem_5rem_minmax(0,1fr)]"
                >
                  <span className="shrink-0 text-faint">{fmtClock(e.ts)}</span>
                  <span
                    className={`shrink-0 truncate font-semibold uppercase ${
                      LEVEL_COLOR[e.level] ?? "text-muted"
                    }`}
                  >
                    {t(`quant.levels.${e.level}`)}
                  </span>
                  <span className="shrink-0 truncate text-faint">{e.source}</span>
                  <span className="min-w-0 truncate text-ink">{e.message}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
    );
  }

  const scrollClass = fill
    ? "min-h-[8rem] max-h-[min(22rem,calc(100dvh-12rem))] overflow-y-auto overflow-x-hidden"
    : `overflow-y-auto overflow-x-hidden ${height}`;

  return (
    <div
      className={[
        "panel flex flex-col p-4 sm:p-5",
        fill ? "min-h-0 h-full max-h-full overflow-hidden" : "",
      ].join(" ")}
    >
      <header className="mb-3 flex flex-none flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <FontAwesomeIcon icon={faTerminal} className="h-4 w-4 text-accent" />
          <h3 className="text-sm font-semibold">{resolvedTitle}</h3>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {LEVELS.map((lv) => (
            <button
              key={lv}
              type="button"
              onClick={() => setLevel(lv)}
              className={[
                "rounded-xl px-2 py-1 font-mono text-[11px] uppercase transition-colors",
                level === lv
                  ? "bg-elevated text-ink"
                  : "text-faint hover:text-ink",
              ].join(" ")}
            >
              {t(`quant.levels.${lv}`)}
            </button>
          ))}
        </div>
      </header>

      <div className="mb-3 flex flex-none items-center gap-2 rounded-xl border border-line bg-base px-3 py-1.5">
        <FontAwesomeIcon
          icon={faMagnifyingGlass}
          className="h-3 w-3 shrink-0 text-faint"
        />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("common.filterPlaceholder")}
          className="w-full min-w-0 bg-transparent text-xs text-ink placeholder:text-faint focus:outline-none"
        />
      </div>

      <div
        className={`rounded-xl border border-line bg-base p-3 font-mono text-xs ${scrollClass}`}
      >
        {filtered.length === 0 ? (
          <p className="text-faint">{t("common.noEvents")}</p>
        ) : (
          <ul className="space-y-2">
            {filtered.map((e, i) => (
              <li key={`${e.ts}-${i}`} className="border-b border-line/50 pb-2 last:border-0 last:pb-0">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-[10px]">
                  <span className="shrink-0 text-faint">{fmtClock(e.ts)}</span>
                  <span
                    className={`shrink-0 font-semibold uppercase ${
                      LEVEL_COLOR[e.level] ?? "text-muted"
                    }`}
                  >
                    {t(`quant.levels.${e.level}`)}
                  </span>
                  <span className="shrink-0 text-faint">{e.source}</span>
                </div>
                <p className="mt-0.5 text-xs leading-snug text-ink">{e.message}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
