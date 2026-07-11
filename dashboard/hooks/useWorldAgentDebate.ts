"use client";

import { useMemo } from "react";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";
import { useWorldSim } from "@/contexts/WorldSimContext";

export interface WorldAgentLine {
  id: string;
  ts: string;
  simDay?: number;
  actor: string;
  country: string;
  text: string;
  level: string;
  source: string;
}

const ACTOR_VI: Record<string, string> = {
  "Corporate AI": "AI doanh nghiệp",
  "Government AI": "AI chính phủ",
  "Societal AI": "AI xã hội",
  Sovereign: "Đại diện chủ quyền",
  Market: "Thị trường",
};

const MAX_LINES = 50;

export function useWorldAgentDebate() {
  const { snapshot } = useMetrics();
  const { locale, countryName } = useLocale();
  const { policyAgentLines } = useWorldSim();

  const lines: WorldAgentLine[] = useMemo(() => {
    const policy: WorldAgentLine[] = policyAgentLines.map((row) => ({
      id: row.id,
      ts: row.ts,
      simDay: row.simDay,
      actor: row.actor,
      country: row.country,
      text: row.text,
      level: row.level,
      source: row.source,
    }));

    const raw = snapshot?.world_agents ?? [];
    const seen = new Set<string>();
    const out: WorldAgentLine[] = [...policy];
    for (let i = 0; i < raw.length && out.length < MAX_LINES; i++) {
      const row = raw[i];
      const rowLocale = (row as { locale?: string }).locale;
      if (rowLocale && rowLocale !== locale && rowLocale.slice(0, 2) !== locale) {
        continue;
      }
      const text = row.text ?? "";
      const sig = `${row.actor}|${text.slice(0, 80)}`;
      if (seen.has(sig)) continue;
      seen.add(sig);
      out.push({
        id: `${row.ts}-${i}`,
        ts: row.ts,
        simDay: row.sim_day,
        actor: row.actor || row.source || "agent",
        country: row.country || "",
        text,
        level: row.level || "info",
        source: row.source || "",
      });
    }
    return out.slice(0, MAX_LINES);
  }, [snapshot?.world_agents, policyAgentLines, locale]);

  const localizedLines = useMemo(
    () =>
      lines.map((line) => ({
        ...line,
        actorLabel:
          locale === "vi"
            ? ACTOR_VI[line.actor] ?? line.actor
            : line.actor,
        countryLabel: line.country
          ? countryName(line.country, line.country)
          : "",
      })),
    [lines, locale, countryName],
  );

  return {
    lines: localizedLines,
    live: lines.length > 0,
    policyLive: policyAgentLines.length > 0,
  };
}
