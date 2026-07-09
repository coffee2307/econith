"use client";

/**
 * ECONITH World :: 50-nation simulation state slice.
 *
 * Owns the Hub & Proxy engine (`lib/worldModel`) and exposes a decoupled,
 * React-friendly view of the world:
 *
 *   - `countries`   projected `CountryMacro` map for ALL 50 nations (fully
 *                   editable — no "observed" lock),
 *   - `tariffs`     the pairwise tariff matrix,
 *   - `events`      the THROTTLED, human-readable log (LogQueue drains one entry
 *                   every `REVEAL_MS`, regardless of how fast the engine ticks),
 *   - `pendingCount` how many escalations are still queued behind the reveal,
 *   - `editFeature` / `imposeTariff` / `resetOverrides` mutation APIs that run
 *     the cross-node cascade logic and enqueue narrative events.
 *
 * The engine is a mutable `NodeMap` held in a ref; React state carries only the
 * cheap projected snapshots. The tick cadence follows the backend Time Engine
 * (`useMetrics().snapshot.time`) so speed 1x–20x stays consistent.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CountryMacro } from "@/hooks/useMetricsStream";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";
import { mutateCountry, setTariff as apiSetTariff } from "@/lib/api";
import type { MacroFeature } from "@/constants/macroFeatures";
import { isSimNation } from "@/constants/simNations";
import { HUB_CODES } from "@/constants/worldGraph";
import {
  applyEdit,
  applyTariff,
  buildWorld,
  ensureNode,
  projectCountries,
  releaseOverrides,
  stepWorld,
  type FieldAddr,
  type SimEvent,
} from "@/lib/worldModel";
import {
  buildPolicyEditReactions,
  buildTariffReactions,
  type PolicyAgentLine,
} from "@/lib/worldAgentReactions";

const REVEAL_MS_BASE = 5000;
const MAX_VISIBLE = 20;
const MAX_PENDING = 8;
const EVENT_COOLDOWN_MS_BASE = 45_000;
const POLICY_COOLDOWN_MS_BASE = 8_000;
const MACRO_PULSE_MS_BASE = 40_000;
const BACKEND_DEDUP_MS_BASE = 90_000;
const MAX_PENDING_BADGE = 8;
const BACKEND_CODES = new Set(["USA", "CHN", "VNM", "JPN", "IND", "DEU"]);
const HUB_SET = new Set<string>(HUB_CODES);

/** Wall-clock interval scaled inversely by sim speed (20x → 4x faster events). */
function scaledMs(baseMs: number, multiplier: number, floorMs: number): number {
  const m = Math.max(1, multiplier);
  return Math.max(floorMs, Math.round(baseMs / m));
}

const IMPORTANT_MESSAGE_KEYS = new Set([
  "unrest",
  "proxyOverride",
  "hubAdjust",
  "proxyContagion",
  "tariffChange",
  "supplyDiversion",
  "geopoliticsAlert",
  "emergency",
  "macroPulse",
]);

function isImportantEvent(e: Omit<SimEvent, "id" | "ts">): boolean {
  if (e.level === "danger" || e.level === "warn") return true;
  if (e.messageKey === "macroPulse") return true;
  if (IMPORTANT_MESSAGE_KEYS.has(e.messageKey)) return true;
  return false;
}

function cooldownFor(e: Omit<SimEvent, "id" | "ts">, multiplier: number): number {
  if (e.messageKey === "macroPulse") {
    return scaledMs(MACRO_PULSE_MS_BASE, multiplier, 3_000);
  }
  if (["hubAdjust", "proxyOverride", "tariffChange", "supplyDiversion", "proxyContagion"].includes(e.messageKey)) {
    return scaledMs(POLICY_COOLDOWN_MS_BASE, multiplier, 1_500);
  }
  return scaledMs(EVENT_COOLDOWN_MS_BASE, multiplier, 5_000);
}

function eventFingerprint(e: Omit<SimEvent, "id" | "ts">): string {
  return `${e.messageKey}|${e.country}|${e.source}|${JSON.stringify(e.messageParams)}`;
}

export interface WorldSim {
  countries: Record<string, CountryMacro>;
  tariffs: Record<string, Record<string, number>>;
  events: SimEvent[];
  pendingCount: number;
  editFeature: (code: string, feature: MacroFeature, uiValue: number) => void;
  imposeTariff: (src: string, dst: string, rate: number) => void;
  resetOverrides: (code: string) => void;
  isOverridden: (code: string, featureKey: string) => boolean;
  tierOf: (code: string) => "hub" | "proxy" | null;
  ensure: (code: string) => void;
  policyAgentLines: PolicyAgentLine[];
}

const WorldSimContext = createContext<WorldSim | null>(null);

function addrOf(feature: MacroFeature): FieldAddr {
  return { group: feature.group === "top" ? "top" : feature.group, field: feature.field };
}

export function WorldSimProvider({ children }: { children: React.ReactNode }) {
  const { snapshot } = useMetrics();
  const { locale } = useLocale();
  const running = snapshot?.time?.running ?? true;
  const multiplier = snapshot?.time?.multiplier ?? 1;
  const multiplierRef = useRef(multiplier);
  useEffect(() => {
    multiplierRef.current = multiplier;
  }, [multiplier]);

  // Mutable engine (never re-created); React state holds cheap projections.
  const worldRef = useRef(buildWorld());
  const tariffsRef = useRef<Record<string, Record<string, number>>>({});
  const pendingRef = useRef<SimEvent[]>([]);
  const seqRef = useRef(0);
  const recentFingerprintsRef = useRef<Map<string, number>>(new Map());
  const seenBackendRef = useRef<Map<string, number>>(new Map());
  const macroPulseRef = useRef(0);

  const [countries, setCountries] = useState<Record<string, CountryMacro>>(() =>
    projectCountries(worldRef.current),
  );
  const [tariffs, setTariffs] = useState<Record<string, Record<string, number>>>({});
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [policyAgentLines, setPolicyAgentLines] = useState<PolicyAgentLine[]>([]);

  const project = useCallback(() => {
    setCountries(projectCountries(worldRef.current));
  }, []);

  const enqueue = useCallback((raw: Omit<SimEvent, "id" | "ts">[]) => {
    if (!raw.length) return;
    const now = Date.now();
    const stamped: SimEvent[] = [];
    for (const e of raw) {
      if (!isImportantEvent(e)) continue;
      const fp = eventFingerprint(e);
      const last = recentFingerprintsRef.current.get(fp) ?? 0;
      const cd = cooldownFor(e, multiplierRef.current);
      if (now - last < cd) continue;
      recentFingerprintsRef.current.set(fp, now);
      stamped.push({
        ...e,
        id: `evt-${seqRef.current++}`,
        ts: now,
      });
    }
    if (!stamped.length) return;
    if (recentFingerprintsRef.current.size > 200) {
      const cutoff = now - scaledMs(EVENT_COOLDOWN_MS_BASE, multiplierRef.current, 5_000) * 2;
      for (const [k, t] of recentFingerprintsRef.current) {
        if (t < cutoff) recentFingerprintsRef.current.delete(k);
      }
    }
    // Backpressure: when the queue is full, only admit danger-level escalations.
    let admitted = stamped;
    if (pendingRef.current.length >= MAX_PENDING) {
      admitted = stamped.filter((e) => e.level === "danger");
      if (!admitted.length) return;
    }
    const next = [...pendingRef.current, ...admitted];
    // Drop the oldest if the queue overflows (keeps the newest escalations).
    pendingRef.current = next.slice(-MAX_PENDING);
    setPendingCount(pendingRef.current.length);
  }, []);

  /** User policy edits: show events + agent lines immediately (no reveal queue / cooldown). */
  const emitPolicyBundle = useCallback(
    (
      raw: Omit<SimEvent, "id" | "ts">[],
      agents: Omit<PolicyAgentLine, "id">[],
    ) => {
      if (!raw.length && !agents.length) return;
      const now = Date.now();
      const stamped: SimEvent[] = raw.map((e) => ({
        ...e,
        id: `evt-${seqRef.current++}`,
        ts: now,
      }));
      if (stamped.length) {
        setEvents((prev) => [...stamped, ...prev].slice(0, MAX_VISIBLE));
      }
      if (agents.length) {
        const simDay = snapshot?.time?.sim_day;
        setPolicyAgentLines((prev) => [
          ...agents.map((a, i) => ({
            ...a,
            id: `pol-${now}-${i}`,
            simDay: a.simDay ?? simDay,
          })),
          ...prev,
        ].slice(0, 16));
      }
    },
    [snapshot?.time?.sim_day],
  );

  // ---- backend headlines + journalist (warn/danger) for Sự kiện tab ----
  useEffect(() => {
    const stream = snapshot?.world_events ?? [];
    const now = Date.now();
    for (const raw of stream) {
      const level = (["info", "ok", "warn", "danger"] as const).includes(
        raw.level as "info",
      )
        ? (raw.level as SimEvent["level"])
        : "info";
      if (level !== "warn" && level !== "danger" && raw.source !== "journalist") {
        continue;
      }
      const key = `${raw.source}|${raw.message?.slice(0, 120)}`;
      const last = seenBackendRef.current.get(key) ?? 0;
      const dedupMs = scaledMs(BACKEND_DEDUP_MS_BASE, multiplier, 12_000);
      if (now - last < dedupMs) continue;
      seenBackendRef.current.set(key, now);
      const messageKey =
        raw.source === "journalist" ? "journalist" : "geopoliticsAlert";
      enqueue([
        {
          level,
          source: raw.source || "world",
          country: "Global",
          messageKey,
          messageParams: { text: raw.message },
        },
      ]);
    }
  }, [snapshot?.world_events, enqueue, multiplier]);

  // ---- periodic macro pulse (localized, client sim) — scales with speed ----
  useEffect(() => {
    if (!running) return;
    const pulseMs = scaledMs(MACRO_PULSE_MS_BASE, multiplier, 3_000);
    const tick = () => {
      const hubs = HUB_CODES.filter((c) => worldRef.current.has(c));
      if (!hubs.length) return;
      const code = hubs[macroPulseRef.current % hubs.length];
      macroPulseRef.current += 1;
      const node = worldRef.current.get(code);
      if (!node) return;
      enqueue([
        {
          level: "info",
          source: "world",
          country: code,
          messageKey: "macroPulse",
          messageParams: {
            country: code,
            growth: (node.gdp_growth * 100).toFixed(1),
            inflation: (node.vectors.monetary.inflation_cpi * 100).toFixed(1),
          },
        },
      ]);
    };
    const id = setInterval(tick, pulseMs);
    const boot = setTimeout(tick, Math.min(4_000, pulseMs));
    return () => {
      clearInterval(id);
      clearTimeout(boot);
    };
  }, [running, enqueue, multiplier]);

  // ---- engine tick loop (fast) : advance macro state on the sim cadence ----
  useEffect(() => {
    if (!running) return;
    const cadence = Math.max(300, 1200 / Math.sqrt(multiplier));
    const dt = 0.5 * Math.sqrt(multiplier);
    const id = setInterval(() => {
      const spontaneous = stepWorld(worldRef.current, dt);
      enqueue(spontaneous);
      project();
    }, cadence);
    return () => clearInterval(id);
  }, [running, multiplier, enqueue, project]);

  // ---- LogQueue drainer : reveal scales with sim speed ----
  useEffect(() => {
    const revealMs = scaledMs(REVEAL_MS_BASE, multiplier, 600);
    const id = setInterval(() => {
      const queue = pendingRef.current;
      if (queue.length === 0) return;
      const next = queue.shift()!;
      pendingRef.current = queue;
      setPendingCount(queue.length);
      setEvents((prev) => [next, ...prev].slice(0, MAX_VISIBLE));
    }, revealMs);
    return () => clearInterval(id);
  }, [multiplier]);

  // ---- mutation APIs ------------------------------------------------------
  const editFeature = useCallback(
    (code: string, feature: MacroFeature, uiValue: number) => {
      // UI shows percentages for fraction fields; the model stores decimals.
      const native = feature.fraction ? uiValue / 100 : uiValue;
      const addr = addrOf(feature);
      const labelKey = feature.key;
      const { events: evs, meta } = applyEdit(
        worldRef.current, code, addr, native, feature.key, !!feature.fraction,
      );
      const agents = buildPolicyEditReactions({
        locale,
        simDay: snapshot?.time?.sim_day,
        country: code,
        labelKey,
        fraction: !!feature.fraction,
        newValue: native,
        meta,
      });
      emitPolicyBundle(evs, agents);
      project();
      // Best-effort forward to the backend for the nations it actually simulates,
      // so the Quant feedback loop still fires for those hubs.
      if (BACKEND_CODES.has(code)) {
        const group = feature.group === "top" ? "" : feature.group;
        void mutateCountry(code, group, feature.field, native);
      }
    },
    [emitPolicyBundle, locale, project, snapshot?.time?.sim_day],
  );

  const imposeTariff = useCallback(
    (src: string, dst: string, rate: number) => {
      const prev = tariffsRef.current[src]?.[dst] ?? 0;
      const nextMatrix = {
        ...tariffsRef.current,
        [src]: { ...(tariffsRef.current[src] ?? {}), [dst]: rate },
      };
      tariffsRef.current = nextMatrix;
      setTariffs(nextMatrix);
      const { events: evs, meta } = applyTariff(worldRef.current, src, dst, rate, prev);
      const agents = buildTariffReactions({
        locale,
        simDay: snapshot?.time?.sim_day,
        meta,
      });
      emitPolicyBundle(evs, agents);
      project();
      if (BACKEND_CODES.has(src) && BACKEND_CODES.has(dst)) {
        void apiSetTariff(src, dst, rate);
      }
    },
    [emitPolicyBundle, locale, project, snapshot?.time?.sim_day],
  );

  const resetOverrides = useCallback(
    (code: string) => {
      releaseOverrides(worldRef.current, code);
      project();
    },
    [project],
  );

  const isOverridden = useCallback((code: string, featureKey: string) => {
    return worldRef.current.get(code)?.overrides.has(featureKey) ?? false;
  }, []);

  const tierOf = useCallback((code: string): "hub" | "proxy" | null => {
    return worldRef.current.get(code)?.tier ?? null;
  }, []);

  const ensure = useCallback(
    (code: string) => {
      if (!code || !isSimNation(code) || worldRef.current.has(code)) return;
      ensureNode(worldRef.current, code);
      project();
    },
    [project],
  );

  const value = useMemo<WorldSim>(
    () => ({
      countries,
      tariffs,
      events,
      pendingCount,
      editFeature,
      imposeTariff,
      resetOverrides,
      isOverridden,
      tierOf,
      ensure,
      policyAgentLines,
    }),
    [countries, tariffs, events, pendingCount, editFeature, imposeTariff,
     resetOverrides, isOverridden, tierOf, ensure, policyAgentLines],
  );

  return <WorldSimContext.Provider value={value}>{children}</WorldSimContext.Provider>;
}

export function useWorldSim(): WorldSim {
  const ctx = useContext(WorldSimContext);
  if (ctx === null) {
    throw new Error("useWorldSim must be used within a <WorldSimProvider>");
  }
  return ctx;
}
