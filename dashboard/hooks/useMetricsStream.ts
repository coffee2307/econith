"use client";

/**
 * ECONITH :: useMetricsStream
 *
 * Manages the lifecycle of the live connection to the backend_core metrics
 * WebSocket (`/api/v1/stream/metrics`). It parses the consolidated snapshot
 * pushed at 5 Hz and exposes it reactively, with robust exponential-backoff
 * auto-reconnection so the UI self-heals across backend reloads.
 */
import { useEffect, useRef, useState } from "react";
import { browserWebSocketUrl } from "@/lib/runtime-url";

// --- payload contract (mirrors core/telemetry.MetricsHub.snapshot) ----------
export interface TimeState {
  sim_day: number;
  multiplier: number;
  running: boolean;
}

export interface MarketState {
  symbol: string | null;
  price: number | null;
  mid: number | null;
  best_bid: number | null;
  best_ask: number | null;
  obi: number | null;
  bid_volume: number | null;
  ask_volume: number | null;
  volume_delta: number | null;
  buy_volume: number | null;
  sell_volume: number | null;
  trade_count: number | null;
  source?: "binance_public" | "binance_public_simulation" | "synthetic_mock" | "unknown";
  event_ms?: number | null;
}

export interface AltState {
  funding_rate?: number | null;
  time_to_funding_s?: number | null;
  open_interest?: number | null;
  oi_change_pct?: number | null;
  liquidation_notional?: number | null;
}

export interface AttributionEntry {
  feature: string;
  importance?: number;
  weight?: number;
}


export interface AiExplain {
  action?: string;
  direction?: number;
  method?: string;
  attribution?: AttributionEntry[];
  top_features?: AttributionEntry[];
}

export interface AiState {
  action?: string; // LONG | SHORT | FLAT
  direction?: number;
  confidence?: number;
  regime?: string;
  regime_confidence?: number;
  weights?: Record<string, number>;
  per_agent?: Record<string, number>;
  explain?: AiExplain;
  agent_brain?: string;
  regime_brain?: string;
}

export interface RoutingLeg {
  symbol: string;
  side: string;
  quantity: number;
  desk: string;
  weight: number;
  reason: string;
}

export interface RoutingState {
  profile?: string;
  confidence?: number;
  direction?: number;
  reduce_only?: boolean;
  created_at?: string;
  legs?: RoutingLeg[];
}

export interface DebateVote {
  agent: string;
  bias: number;
  confidence: number;
  rationale?: string;
}

export interface DebateState {
  consensus_bias?: number;
  consensus_confidence?: number;
  sources?: string[];
  votes?: DebateVote[];
  dissent?: Record<string, number>;
}

export interface AlphaState {
  symbol?: string;
  direction?: number;
  confidence?: number;
  agent?: string;
}

// Each vector is a flat map of feature -> value (100+ features total).
export type MacroVector = Record<string, number>;

export interface CountryVectors {
  monetary: MacroVector;
  fiscal: MacroVector;
  labor: MacroVector;
  industrial: MacroVector;
  geopolitical: MacroVector;
}

export interface CountryMacro {
  code?: string;
  name: string;
  continent?: string;
  gdp?: number;
  gdp_per_capita?: number;
  gdp_growth: number;
  inflation: number;
  interest_rate: number;
  tax: number;
  population: number;
  unemployment: number;
  vectors?: CountryVectors;
}

export interface WorldGlobalMacro {
  gdp_growth: number;
  inflation: number;
  interest_rate: number;
  tax: number;
  unemployment: number;
  population: number;
  gdp?: number;
  trade_tension?: number;
}

export interface WorldState {
  sim_day?: number;
  scale?: {
    countries: number;
    population_clusters: number;
    strata_per_country: number;
  };
  global?: WorldGlobalMacro;
  countries?: Record<string, CountryMacro>;
  tariffs?: Record<string, Record<string, number>>;
  alliances?: Record<string, Record<string, number>>;
}

export interface SentinelState {
  state?: string; // CLOSED | OPEN | HALF_OPEN
  mode?: string; // NORMAL | REDUCE_ONLY | FROZEN
  equity?: number;
  peak_equity?: number;
  drawdown?: number;
  var?: number;
  cvar?: number;
  var_method?: string;
  latency_ms?: number;
  last_price?: number;
  breaker_reason?: string;
}

export interface LogEvent {
  ts: string;
  level: string; // info | ok | warn | danger
  source: string;
  message: string;
}

export interface WorldAgentEvent {
  ts: string;
  sim_day?: number;
  actor: string;
  country: string;
  text: string;
  text_vi?: string;
  level: string;
  source: string;
  locale?: string;
  provenance?: string;
}

export type QuantModeName = "REALITY" | "SIMULATION";

export interface QuantModeState {
  mode: QuantModeName;
  coupling_enabled: boolean;
  anomaly_injection_enabled: boolean;
}

export interface MetricsSnapshot {
  ts: string;
  time: TimeState;
  market: MarketState;
  alt: AltState;
  ai: AiState;
  routing?: RoutingState;
  debate?: DebateState;
  alpha?: AlphaState;
  world: WorldState;
  sentinel: SentinelState;
  events: LogEvent[];
  world_events?: LogEvent[];
  world_agents?: WorldAgentEvent[];
  world_dialogue?: Array<Record<string, unknown>>;
  quant_mode?: QuantModeState;
}

export type ConnectionStatus =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

export const DEFAULT_WS_URL = process.env.NEXT_PUBLIC_WS_URL;

interface UseMetricsStreamOptions {
  url?: string;
  baseBackoffMs?: number;
  maxBackoffMs?: number;
}

export interface MetricsStream {
  snapshot: MetricsSnapshot | null;
  status: ConnectionStatus;
  attempts: number;
}

export function useMetricsStream(
  options: UseMetricsStreamOptions = {},
): MetricsStream {
  const requestedUrl = options.url ?? DEFAULT_WS_URL;
  const baseBackoff = options.baseBackoffMs ?? 500;
  const maxBackoff = options.maxBackoffMs ?? 10_000;

  const [snapshot, setSnapshot] = useState<MetricsSnapshot | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [attempts, setAttempts] = useState(0);

  // Keep latest setters stable for the long-lived effect closure.
  const attemptRef = useRef(0);
  const lastFrameAtRef = useRef(0);
  const fallbackInFlightRef = useRef(false);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const url = browserWebSocketUrl(
      "/api/v1/stream/metrics",
      requestedUrl,
    );

    let stopped = false;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let fallbackTimer: ReturnType<typeof setInterval> | null = null;

    // The dashboard normally receives five WebSocket snapshots per second.
    // A short REST fallback prevents an authenticated proxy, sleeping tab, or
    // transient WebSocket upgrade issue from leaving World / Quant blank even
    // while the backend is healthy and already publishing telemetry.
    const refreshFallback = async () => {
      if (stopped || fallbackInFlightRef.current) return;
      fallbackInFlightRef.current = true;
      try {
        const res = await fetch("/api/v1/metrics", {
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (!res.ok) return;
        const parsed = (await res.json()) as MetricsSnapshot;
        if (!stopped) setSnapshot(parsed);
      } catch {
        // WebSocket retry remains the primary recovery path.
      } finally {
        fallbackInFlightRef.current = false;
      }
    };

    const open = () => {
      if (stopped) return;
      setStatus(attemptRef.current === 0 ? "connecting" : "reconnecting");
      try {
        ws = new WebSocket(url);
      } catch {
        retry();
        return;
      }

      ws.onopen = () => {
        attemptRef.current = 0;
        setAttempts(0);
        setStatus("open");
      };

      ws.onmessage = (event: MessageEvent) => {
        try {
          const parsed = JSON.parse(event.data as string) as MetricsSnapshot;
          lastFrameAtRef.current = Date.now();
          setSnapshot(parsed);
        } catch {
          // ignore malformed frame
        }
      };

      // A socket error is always followed by close; trigger close to reconnect.
      ws.onerror = () => {
        try {
          ws?.close();
        } catch {
          // noop
        }
      };

      ws.onclose = () => {
        if (!stopped) retry();
      };
    };

    const retry = () => {
      if (stopped) return;
      setStatus("reconnecting");
      const exp = baseBackoff * Math.pow(2, attemptRef.current);
      const jitter = Math.random() * 250;
      const delay = Math.min(maxBackoff, exp) + jitter;
      attemptRef.current += 1;
      setAttempts(attemptRef.current);
      timer = setTimeout(open, delay);
    };

    // Populate the first render immediately, then poll only if the WebSocket
    // has not delivered a frame for three seconds.
    void refreshFallback();
    fallbackTimer = setInterval(() => {
      if (Date.now() - lastFrameAtRef.current > 3_000) {
        void refreshFallback();
      }
    }, 1_000);

    open();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (fallbackTimer) clearInterval(fallbackTimer);
      if (ws) {
        ws.onclose = null; // prevent reconnect on intentional teardown
        ws.onerror = null;
        ws.onmessage = null;
        try {
          ws.close();
        } catch {
          // noop
        }
      }
    };
  }, [requestedUrl, baseBackoff, maxBackoff]);

  return { snapshot, status, attempts };
}
