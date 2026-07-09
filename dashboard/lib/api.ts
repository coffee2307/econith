/**
 * ECONITH :: backend_core REST helper.
 *
 * Thin wrapper over the control endpoints exposed by main.py. All calls are
 * best-effort: failures are swallowed (returning null) so transient backend
 * reloads never crash the UI — the live WebSocket reflects the real state.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

async function post<T = unknown>(
  path: string,
  body?: Record<string, unknown>,
): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export type AnomalyKind = "shock" | "latency" | "vol";

export const sentinelInject = (kind: AnomalyKind) =>
  post("/sentinel/inject", { kind });

export const sentinelReset = () => post("/sentinel/reset");

// --- Quant operating mode (REALITY vs SIMULATION) ----------------------------
export type QuantModeName = "REALITY" | "SIMULATION";

export interface QuantModeState {
  mode: QuantModeName;
  coupling_enabled: boolean;
  anomaly_injection_enabled: boolean;
}

export const getQuantMode = () => get<QuantModeState>("/mode");

export const setQuantMode = (mode: QuantModeName) =>
  post<QuantModeState>("/mode", { mode });

export const setTimeSpeed = (multiplier: number) =>
  post("/time/speed", { multiplier });

export const pauseTime = () => post("/time/pause");

export const resumeTime = () => post("/time/resume");

export interface ScenarioResult {
  prompt: string;
  mutations: { country: string; field: string; value: number }[];
  applied: string[];
}

export const runScenario = (prompt: string) =>
  post<ScenarioResult>("/world/scenario", { prompt });

// --- ECONITH World direct state mutators -------------------------------------
export const mutateCountry = (
  code: string,
  group: string,
  field: string,
  value: number,
) =>
  post(`/world/country/${code}/mutate`, { group, field, value });

export const setTariff = (source: string, target: string, value: number) =>
  post("/world/tariff", { source, target, value });

async function get<T = unknown>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

export const getWorldState = () => get("/world/state");
export const getCountry = (code: string) => get(`/world/country/${code}`);
export const getJournalistNews = (limit = 20) =>
  get<{ news: Array<{ ts: string; category: string; level: string; message: string }> }>(
    `/journalist/news?limit=${limit}`,
  );

export interface AgentExchangeLine {
  agent_id: string;
  country: string;
  role: string;
  text: string;
}

export const postAgentExchange = (body: {
  locale: string;
  topic?: string;
  countries: Record<string, unknown>;
}) => post<{ lines: AgentExchangeLine[]; source: string; locale: string }>(
  "/world/agent-exchange",
  body,
);

export const syncLocale = (locale: string) =>
  post<{ locale: string }>("/locale", { locale });
