"use client";

/**
 * ECONITH World :: metrics/API adapter (backend is the sole simulation SoT).
 *
 * Previously this context ran a client-side 50-nation Hub/Proxy engine
 * (`stepWorld`, macroPulse, worldAgentReactions) that diverged from the
 * backend WorldKernel. That dual-engine split produced hardcoded, fake-looking
 * event logs. The context is now a thin view/mutation adapter over
 * `snapshot.world` + `world_events` / `world_agents`.
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
import {
  getControlState,
  mutateCountry,
  setTariff as apiSetTariff,
} from "@/lib/api";
import type { MacroFeature } from "@/constants/macroFeatures";
import { isSimNation } from "@/constants/simNations";
import { LIVE_BACKEND_CODES } from "@/constants/liveWorld";
import { tierOf as graphTierOf } from "@/constants/worldGraph";
import type { SimEvent } from "@/lib/worldModel";
import type { PolicyAgentLine } from "@/lib/worldAgentReactions";

const MAX_VISIBLE = 60;
const BACKEND_DEDUP_MS = 12_000;
/** Live backend macros today — only these accept mutate/tariff. */
const BACKEND_CODES = new Set<string>(LIVE_BACKEND_CODES);

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
  backendLive: (code: string) => boolean;
}

const WorldSimContext = createContext<WorldSim | null>(null);

function addrGroup(feature: MacroFeature): string {
  return feature.group === "top" ? "" : feature.group;
}

export function WorldSimProvider({ children }: { children: React.ReactNode }) {
  const { snapshot } = useMetrics();
  const { locale } = useLocale();
  const [worldEnabled, setWorldEnabled] = useState(true);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [draftOverrides, setDraftOverrides] = useState<Record<string, Record<string, number>>>({});
  const [policyAgentLines, setPolicyAgentLines] = useState<PolicyAgentLine[]>([]);
  const seenBackendRef = useRef<Map<string, number>>(new Map());
  const seqRef = useRef(0);

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      const state = await getControlState();
      if (mounted && state) {
        setWorldEnabled(state.world_simulation_enabled);
      }
    };
    void refresh();
    const id = setInterval(refresh, 5000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, []);

  const countries = useMemo<Record<string, CountryMacro>>(() => {
    const raw = snapshot?.world?.countries ?? {};
    return raw as Record<string, CountryMacro>;
  }, [snapshot?.world?.countries]);

  const tariffs = useMemo<Record<string, Record<string, number>>>(
    () => snapshot?.world?.tariffs ?? {},
    [snapshot?.world?.tariffs],
  );

  // ---- backend world_events → Event Log (includes Journalist info) ----------
  useEffect(() => {
    if (!worldEnabled) return;
    const stream = snapshot?.world_events ?? [];
    const now = Date.now();
    const admitted: SimEvent[] = [];
    for (const raw of stream) {
      const level = (["info", "ok", "warn", "danger"] as const).includes(
        raw.level as "info",
      )
        ? (raw.level as SimEvent["level"])
        : "info";
      const source = raw.source || "world";
      const message = String(raw.message ?? "").trim();
      if (!message) continue;
      // Strip "[sim-date] Country: " so dated system.log and plain headline match.
      const normalized = message
        .replace(/^\[[^\]]+\]\s*/, "")
        .replace(/^[^:]+:\s*/, "")
        .slice(0, 120);
      const key = `${source}|${normalized}`;
      const last = seenBackendRef.current.get(key) ?? 0;
      if (now - last < BACKEND_DEDUP_MS) continue;
      seenBackendRef.current.set(key, now);
      const messageKey =
        source === "journalist" ? "journalist" : "geopoliticsAlert";
      admitted.push({
        id: `be-${seqRef.current++}`,
        ts: Date.parse(raw.ts) || now,
        level,
        source,
        country: "Global",
        messageKey,
        messageParams: { text: message },
      });
    }
    if (!admitted.length) return;
    if (seenBackendRef.current.size > 300) {
      const cutoff = now - BACKEND_DEDUP_MS * 4;
      for (const [k, t] of seenBackendRef.current) {
        if (t < cutoff) seenBackendRef.current.delete(k);
      }
    }
    // Stream arrives newest-first; prepend new unique rows.
    setEvents((prev) => {
      const existing = new Set(prev.map((e) => `${e.source}|${JSON.stringify(e.messageParams)}`));
      const fresh = admitted.filter(
        (e) => !existing.has(`${e.source}|${JSON.stringify(e.messageParams)}`),
      );
      if (!fresh.length) return prev;
      return [...fresh, ...prev].slice(0, MAX_VISIBLE);
    });
  }, [snapshot?.world_events, worldEnabled]);

  const editFeature = useCallback(
    (code: string, feature: MacroFeature, uiValue: number) => {
      const native = feature.fraction ? uiValue / 100 : uiValue;
      setDraftOverrides((prev) => ({
        ...prev,
        [code]: { ...(prev[code] ?? {}), [feature.key]: uiValue },
      }));
      if (!BACKEND_CODES.has(code)) {
        // Topology-only nation: keep local draft, do not fake a cascade.
        return;
      }
      const group = addrGroup(feature);
      void mutateCountry(code, group, feature.field, native).then(() => {
        setDraftOverrides((prev) => {
          const next = { ...prev };
          if (next[code]) {
            const copy = { ...next[code] };
            delete copy[feature.key];
            if (Object.keys(copy).length) next[code] = copy;
            else delete next[code];
          }
          return next;
        });
      });
      const now = Date.now();
      const text =
        locale === "vi"
          ? `${code}: đã gửi điều chỉnh ${feature.key} = ${uiValue.toFixed(2)} tới backend.`
          : `${code}: submitted ${feature.key} = ${uiValue.toFixed(2)} to backend.`;
      setPolicyAgentLines((prev) =>
        [
          {
            id: `pol-${now}`,
            ts: new Date(now).toISOString(),
            simDay: snapshot?.time?.sim_day,
            actor: "Government AI",
            country: code,
            text,
            level: "warn" as const,
            source: "policy" as const,
          },
          ...prev,
        ].slice(0, 12),
      );
    },
    [locale, snapshot?.time?.sim_day],
  );

  const imposeTariff = useCallback(
    (src: string, dst: string, rate: number) => {
      if (!BACKEND_CODES.has(src) || !BACKEND_CODES.has(dst)) return;
      void apiSetTariff(src, dst, rate);
      const now = Date.now();
      const pct = `${(rate * 100).toFixed(0)}%`;
      const text =
        locale === "vi"
          ? `${src} áp thuế ${pct} lên ${dst} — đã gửi backend.`
          : `${src} set a ${pct} tariff on ${dst} — submitted to backend.`;
      setPolicyAgentLines((prev) =>
        [
          {
            id: `tar-${now}`,
            ts: new Date(now).toISOString(),
            simDay: snapshot?.time?.sim_day,
            actor: "Government AI",
            country: src,
            text,
            level: "warn" as const,
            source: "policy" as const,
          },
          ...prev,
        ].slice(0, 12),
      );
    },
    [locale, snapshot?.time?.sim_day],
  );

  const resetOverrides = useCallback((code: string) => {
    setDraftOverrides((prev) => {
      const next = { ...prev };
      delete next[code];
      return next;
    });
  }, []);

  const isOverridden = useCallback(
    (code: string, featureKey: string) =>
      Boolean(draftOverrides[code] && featureKey in draftOverrides[code]),
    [draftOverrides],
  );

  const tierOf = useCallback((code: string): "hub" | "proxy" | null => {
    if (!isSimNation(code)) return null;
    return graphTierOf(code);
  }, []);

  const ensure = useCallback((_code: string) => {
    // No client-side node spawning — backend owns the live nation set.
  }, []);

  const backendLive = useCallback((code: string) => BACKEND_CODES.has(code), []);

  const value = useMemo<WorldSim>(
    () => ({
      countries,
      tariffs,
      events,
      pendingCount: 0,
      editFeature,
      imposeTariff,
      resetOverrides,
      isOverridden,
      tierOf,
      ensure,
      policyAgentLines,
      backendLive,
    }),
    [
      countries,
      tariffs,
      events,
      editFeature,
      imposeTariff,
      resetOverrides,
      isOverridden,
      tierOf,
      ensure,
      policyAgentLines,
      backendLive,
    ],
  );

  return (
    <WorldSimContext.Provider value={value}>{children}</WorldSimContext.Provider>
  );
}

export function useWorldSim(): WorldSim {
  const ctx = useContext(WorldSimContext);
  if (ctx === null) {
    throw new Error("useWorldSim must be used within a <WorldSimProvider>");
  }
  return ctx;
}
