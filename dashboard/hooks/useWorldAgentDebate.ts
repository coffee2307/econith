"use client";

import { useMemo } from "react";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";
import { useWorldSim } from "@/contexts/WorldSimContext";

export interface GroundedMetricChip {
  name: string;
  value: number;
  unit?: string;
}

export interface WorldAgentLine {
  id: string;
  ts: string;
  simDay?: number;
  actor: string;
  country: string;
  text: string;
  level: string;
  source: string;
  provenance?: string;
  metrics?: GroundedMetricChip[];
}

const ACTOR_VI: Record<string, string> = {
  "Corporate AI": "AI doanh nghiệp",
  "Government AI": "AI chính phủ",
  "Societal AI": "AI xã hội",
  Sovereign: "Đại diện chủ quyền",
  Market: "Thị trường",
  Household: "Hộ gia đình",
  Labor: "Lao động",
  "Central Bank": "Ngân hàng trung ương",
  Dialogue: "Đối thoại",
};

const MAX_LINES = 30;

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
      provenance: "policy",
    }));

    // Prefer structured dialogue turns when present (grounded metrics).
    const dialogueTurns =
      (snapshot as { world_dialogue?: Array<Record<string, unknown>> } | null)
        ?.world_dialogue ??
      (snapshot?.world as { dialogue?: Record<string, unknown> } | undefined)?.dialogue;
    const dialogueLines: WorldAgentLine[] = [];
    const turns = Array.isArray(dialogueTurns)
      ? dialogueTurns
      : dialogueTurns
        ? [dialogueTurns]
        : [];
    for (let ti = 0; ti < turns.length; ti++) {
      const turn = turns[ti] as {
        tick?: number;
        source?: string;
        level?: string;
        utterances?: Array<{
          agent_id?: string;
          role?: string;
          country?: string;
          text?: string;
          locale?: string;
          metrics?: GroundedMetricChip[];
        }>;
      };
      for (let ui = 0; ui < (turn.utterances ?? []).length; ui++) {
        const u = turn.utterances![ui];
        if (u.locale && u.locale.slice(0, 2) !== locale.slice(0, 2)) continue;
        if (!u.text) continue;
        dialogueLines.push({
          id: `dlg-${turn.tick ?? ti}-${ui}`,
          ts: new Date().toISOString(),
          simDay: turn.tick,
          actor: u.role || u.agent_id || "Dialogue",
          country: u.country || "",
          text: u.text,
          level: turn.level || "info",
          source: "dialogue",
          provenance: turn.source,
          metrics: u.metrics,
        });
      }
    }

    const raw = snapshot?.world_agents ?? [];
    const seen = new Set<string>();
    const out: WorldAgentLine[] = [...policy, ...dialogueLines];
    for (let i = 0; i < raw.length && out.length < MAX_LINES; i++) {
      const row = raw[i] as WorldAgentLine & {
        locale?: string;
        sim_day?: number;
        metrics?: GroundedMetricChip[];
        provenance?: string;
      };
      const rowLocale = row.locale;
      if (rowLocale && rowLocale !== locale && rowLocale.slice(0, 2) !== locale) {
        continue;
      }
      const text = row.text ?? "";
      const sig = `${row.actor}|${row.country}|${text.slice(0, 60)}`;
      if (seen.has(sig)) continue;
      seen.add(sig);
      out.push({
        id: `${row.ts}-${i}`,
        ts: row.ts,
        simDay: row.sim_day ?? row.simDay,
        actor: row.actor || row.source || "agent",
        country: row.country || "",
        text,
        level: row.level || "info",
        source: row.source || "",
        provenance: row.provenance,
        metrics: row.metrics,
      });
    }
    return out
      .slice(0, MAX_LINES)
      .sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
  }, [snapshot?.world_agents, snapshot?.world, policyAgentLines, locale]);

  const localizedLines = useMemo(
    () =>
      lines.map((line) => ({
        ...line,
        actorLabel:
          locale === "vi" ? ACTOR_VI[line.actor] ?? line.actor : line.actor,
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
