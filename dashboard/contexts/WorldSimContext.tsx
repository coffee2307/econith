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
import { mutateCountry, setTariff as apiSetTariff } from "@/lib/api";
import type { MacroFeature } from "@/constants/macroFeatures";
import { isSimNation } from "@/constants/simNations";
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

const REVEAL_MS = 1500;        // one log entry surfaces every 1.5s (readability)
const MAX_VISIBLE = 80;        // cap the rendered log
const MAX_PENDING = 200;       // hard backstop on the queue
const BACKEND_CODES = new Set(["USA", "CHN", "VNM", "JPN", "IND", "DEU"]);

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
}

const WorldSimContext = createContext<WorldSim | null>(null);

function addrOf(feature: MacroFeature): FieldAddr {
  return { group: feature.group === "top" ? "top" : feature.group, field: feature.field };
}

export function WorldSimProvider({ children }: { children: React.ReactNode }) {
  const { snapshot } = useMetrics();
  const running = snapshot?.time?.running ?? true;
  const multiplier = snapshot?.time?.multiplier ?? 1;

  // Mutable engine (never re-created); React state holds cheap projections.
  const worldRef = useRef(buildWorld());
  const tariffsRef = useRef<Record<string, Record<string, number>>>({});
  const pendingRef = useRef<SimEvent[]>([]);
  const seqRef = useRef(0);

  const [countries, setCountries] = useState<Record<string, CountryMacro>>(() =>
    projectCountries(worldRef.current),
  );
  const [tariffs, setTariffs] = useState<Record<string, Record<string, number>>>({});
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [pendingCount, setPendingCount] = useState(0);

  const project = useCallback(() => {
    setCountries(projectCountries(worldRef.current));
  }, []);

  const enqueue = useCallback((raw: Omit<SimEvent, "id" | "ts">[]) => {
    if (!raw.length) return;
    const now = Date.now();
    const stamped = raw.map((e) => ({
      ...e,
      id: `evt-${seqRef.current++}`,
      ts: now,
    }));
    const next = [...pendingRef.current, ...stamped];
    // Drop the oldest if the queue overflows (keeps the newest escalations).
    pendingRef.current = next.slice(-MAX_PENDING);
    setPendingCount(pendingRef.current.length);
  }, []);

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

  // ---- LogQueue drainer (slow) : reveal one queued event every REVEAL_MS ----
  useEffect(() => {
    const id = setInterval(() => {
      const queue = pendingRef.current;
      if (queue.length === 0) return;
      const next = queue.shift()!;
      pendingRef.current = queue;
      setPendingCount(queue.length);
      setEvents((prev) => [next, ...prev].slice(0, MAX_VISIBLE));
    }, REVEAL_MS);
    return () => clearInterval(id);
  }, []);

  // ---- mutation APIs ------------------------------------------------------
  const editFeature = useCallback(
    (code: string, feature: MacroFeature, uiValue: number) => {
      // UI shows percentages for fraction fields; the model stores decimals.
      const native = feature.fraction ? uiValue / 100 : uiValue;
      const addr = addrOf(feature);
      const { events: evs } = applyEdit(
        worldRef.current, code, addr, native, feature.key, !!feature.fraction,
      );
      enqueue(evs);
      project();
      // Best-effort forward to the backend for the nations it actually simulates,
      // so the Quant feedback loop still fires for those hubs.
      if (BACKEND_CODES.has(code)) {
        const group = feature.group === "top" ? "" : feature.group;
        void mutateCountry(code, group, feature.field, native);
      }
    },
    [enqueue, project],
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
      const { events: evs } = applyTariff(worldRef.current, src, dst, rate, prev);
      enqueue(evs);
      project();
      if (BACKEND_CODES.has(src) && BACKEND_CODES.has(dst)) {
        void apiSetTariff(src, dst, rate);
      }
    },
    [enqueue, project],
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
    }),
    [countries, tariffs, events, pendingCount, editFeature, imposeTariff,
     resetOverrides, isOverridden, tierOf, ensure],
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
