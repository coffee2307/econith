"use client";

import { useEffect, useRef } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faComments, faSatelliteDish } from "@fortawesome/free-solid-svg-icons";
import { useLocale } from "@/contexts/LocaleContext";
import { useWorldAgentDebate } from "@/hooks/useWorldAgentDebate";

const LEVEL_DOT: Record<string, string> = {
  info: "bg-faint",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
};

function formatMetric(name: string, value: number, unit?: string): string {
  if (unit === "%") return `${name} ${(value * 100).toFixed(1)}%`;
  if (Math.abs(value) < 10) return `${name} ${value.toFixed(2)}`;
  return `${name} ${value.toFixed(1)}`;
}

export function WorldAgentExchange() {
  const { t, locale } = useLocale();
  const { lines, live, policyLive } = useWorldAgentDebate();
  const timeLocale = locale === "vi" ? "vi-VN" : "en-GB";

  const scrollRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
      <div className="flex flex-none items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <div className="flex items-center gap-2">
            <FontAwesomeIcon icon={faComments} className="h-4 w-4 text-world" />
            <h2 className="text-sm font-bold">{t("world.agentExchangeTitle")}</h2>
          </div>
          <p className="mt-0.5 text-[11px] text-muted">
            {t("world.agentExchangeSubtitle")}
          </p>
        </div>
        <span
          className={[
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[9px] uppercase",
            live || policyLive
              ? "bg-emerald-500/10 text-emerald-600"
              : "bg-amber-500/10 text-amber-600",
          ].join(" ")}
        >
          <FontAwesomeIcon icon={faSatelliteDish} className="h-2.5 w-2.5" />
          {live || policyLive
            ? t("world.agentExchangeLive")
            : t("world.agentExchangeWaiting")}
        </span>
      </div>

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto p-4">
        {lines.length === 0 ? (
          <p className="font-mono text-xs text-faint">
            {t("world.agentExchangeEmpty")}
          </p>
        ) : (
          <div className="space-y-3">
            {lines.map((line) => (
              <article
                key={line.id}
                className="rounded-xl border border-line bg-elevated px-3 py-2"
              >
                <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wider text-muted">
                  <span className="flex items-center gap-1.5 font-semibold text-world">
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${LEVEL_DOT[line.level] ?? "bg-faint"}`}
                    />
                    {line.actorLabel}
                    {line.countryLabel ? ` · ${line.countryLabel}` : ""}
                  </span>
                  <span className="font-mono text-faint">
                    {line.provenance ? `${line.provenance} · ` : ""}
                    {line.simDay != null ? `${t("common.day")} ${line.simDay}` : ""}
                    {line.simDay != null ? " · " : ""}
                    {new Date(line.ts).toLocaleTimeString(timeLocale, {
                      hour12: false,
                    })}
                  </span>
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-ink">
                  {line.text}
                </p>
                {line.metrics && line.metrics.length > 0 ? (
                  <div className="mt-2 flex flex-wrap gap-1">
                    {line.metrics.slice(0, 4).map((m, idx) => (
                      <span
                        key={`${line.id}-m-${idx}`}
                        className="rounded-full border border-line bg-surface px-2 py-0.5 font-mono text-[9px] text-muted"
                      >
                        {formatMetric(m.name, m.value, m.unit)}
                      </span>
                    ))}
                  </div>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
