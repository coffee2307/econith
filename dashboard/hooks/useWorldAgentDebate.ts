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
  textVi?: string;
  level: string;
  source: string;
  provenance?: string;
  metrics?: GroundedMetricChip[];
  respondsTo?: string;
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
  Enterprise: "Doanh nghiệp",
  Government: "Chính phủ",
  Dialogue: "Đối thoại",
};

const MAX_LINES = 30;

/** Pick Vietnamese display copy when UI is vi; English source otherwise. */
function displayAgentText(
  locale: string,
  text: string,
  textVi?: string,
): string {
  if (locale === "vi" && textVi && textVi.trim()) return textVi.trim();
  return text;
}

export function useWorldAgentDebate() {
  const { snapshot } = useMetrics();
  const { locale, countryName } = useLocale();
  const { policyAgentLines } = useWorldSim();
  const worldAgentRows = snapshot?.world_agents;
  const worldState = snapshot?.world;
  const worldDialogue = snapshot?.world_dialogue;

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
      worldDialogue ??
      (worldState as { dialogue?: Record<string, unknown> } | undefined)?.dialogue;
    const dialogueLines: WorldAgentLine[] = [];
    const turns = asDialogueTurns(dialogueTurns);
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
          text_vi?: string;
          locale?: string;
          metrics?: GroundedMetricChip[];
          responds_to?: string;
        }>;
      };
      for (let ui = 0; ui < (turn.utterances ?? []).length; ui++) {
        const u = turn.utterances![ui];
        // Do not drop EN utterances when UI is VI — translate via text_vi / show EN.
        if (!u.text) continue;
        dialogueLines.push({
          id: `dlg-${turn.tick ?? ti}-${ui}`,
          ts: new Date().toISOString(),
          simDay: turn.tick,
          actor: u.role || u.agent_id || "Dialogue",
          country: u.country || "",
          text: displayAgentText(locale, u.text, u.text_vi),
          textVi: u.text_vi,
          level: turn.level || "info",
          source: "dialogue",
          provenance: turn.source,
          metrics: u.metrics,
          respondsTo: u.responds_to,
        });
      }
    }

    const raw = worldAgentRows ?? [];
    const out: WorldAgentLine[] = [...policy, ...dialogueLines];
    // Structured dialogue and raw telemetry can describe the same event.
    // Seed the set so the panel shows one readable chain rather than duplicates.
    const seen = new Set(
      out.map((line) => `${line.actor}|${line.country}|${line.text.slice(0, 60)}`),
    );
    const AGENT_ACTORS = new Set([
      "Corporate AI",
      "Government AI",
      "Societal AI",
      "Sovereign",
      "Household",
      "Central Bank",
      "Labor",
      "Dialogue",
    ]);
    for (let i = 0; i < raw.length && out.length < MAX_LINES; i++) {
      const row = raw[i] as WorldAgentLine & {
        locale?: string;
        sim_day?: number;
        metrics?: GroundedMetricChip[];
        provenance?: string;
        text_vi?: string;
        responds_to?: string;
      };
      const actor = row.actor || row.source || "";
      const source = row.source || "";
      // Keep Agents tab distinct from Events: drop Market / regime templates.
      if (actor === "Market" || source === "regime") continue;
      if (
        !AGENT_ACTORS.has(actor) &&
        source !== "corporate" &&
        source !== "government" &&
        source !== "society" &&
        source !== "sovereign" &&
        source !== "dialogue" &&
        source !== "policy" &&
        !(
          typeof row.provenance === "string" &&
          row.provenance.startsWith("hypothesis")
        )
      ) {
        continue;
      }
      const textEn = row.text ?? "";
      const textVi = row.text_vi || row.textVi;
      if (/Chế độ thị trường chuyển sang|Market regime shifted|HMM (flags|đánh dấu)/i.test(textEn)) {
        continue;
      }
      const text = displayAgentText(locale, textEn, textVi);
      const sig = `${row.actor}|${row.country}|${textEn.slice(0, 60)}`;
      if (seen.has(sig)) continue;
      seen.add(sig);
      out.push({
        id: `${row.ts}-${i}`,
        ts: row.ts,
        simDay: row.sim_day ?? row.simDay,
        actor: row.actor || row.source || "agent",
        country: row.country || "",
        text,
        textVi,
        level: row.level || "info",
        source: row.source || "",
        provenance: row.provenance,
        metrics: row.metrics,
        respondsTo: row.responds_to ?? row.respondsTo,
      });
    }
    return out
      .sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
      .slice(0, MAX_LINES);
  }, [
    worldAgentRows,
    worldState,
    worldDialogue,
    policyAgentLines,
    locale,
  ]);

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

function asDialogueTurns(
  dialogueTurns: unknown,
): Array<Record<string, unknown>> {
  if (Array.isArray(dialogueTurns)) return dialogueTurns as Array<Record<string, unknown>>;
  if (dialogueTurns) return [dialogueTurns as Record<string, unknown>];
  return [];
}
