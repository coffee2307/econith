/**
 * ECONITH :: backend_core REST helper.
 *
 * Thin wrapper over the control endpoints exposed by main.py. Transient
 * backend reloads should not crash the UI — callers still receive null on
 * hard network failure — but auth / validation errors are surfaced via
 * ``lastApiError`` so enabling API_AUTH_ENABLED does not fail silently.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

/** Browser-visible API key (set NEXT_PUBLIC_API_KEY to match backend API_KEYS). */
export const API_KEY =
  (process.env.NEXT_PUBLIC_API_KEY ?? "").trim();

export interface ApiErrorInfo {
  path: string;
  status: number;
  detail: string;
}

let lastApiError: ApiErrorInfo | null = null;

export function getLastApiError(): ApiErrorInfo | null {
  return lastApiError;
}

export function clearLastApiError(): void {
  lastApiError = null;
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const headers: Record<string, string> = { ...(extra ?? {}) };
  if (API_KEY) {
    headers["X-API-Key"] = API_KEY;
  }
  return headers;
}

async function readErrorDetail(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown };
    if (typeof data?.detail === "string") return data.detail;
    if (data?.detail != null) return JSON.stringify(data.detail);
  } catch {
    /* ignore */
  }
  return res.statusText || `HTTP ${res.status}`;
}

async function post<T = unknown>(
  path: string,
  body?: Record<string, unknown>,
): Promise<T | null> {
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      lastApiError = {
        path,
        status: res.status,
        detail: await readErrorDetail(res),
      };
      return null;
    }
    clearLastApiError();
    return (await res.json()) as T;
  } catch (err) {
    lastApiError = {
      path,
      status: 0,
      detail: err instanceof Error ? err.message : "network error",
    };
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
    const res = await fetch(`${API_BASE}${path}`, {
      headers: authHeaders(),
    });
    if (!res.ok) {
      lastApiError = {
        path,
        status: res.status,
        detail: await readErrorDetail(res),
      };
      return null;
    }
    clearLastApiError();
    return (await res.json()) as T;
  } catch (err) {
    lastApiError = {
      path,
      status: 0,
      detail: err instanceof Error ? err.message : "network error",
    };
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

// --- Main System Control (system_controller.py) ------------------------------
export type OperatingModeName =
  | "REALITY"
  | "SIMULATION"
  | "AUTONOMOUS_HYPOTHESIS"
  | "USER_HYPOTHESIS"
  | "FULLY_AUTONOMOUS";

export interface SystemControlState {
  operating_mode: OperatingModeName;
  quant_mode: QuantModeName;
  world_simulation_enabled: boolean;
  world_to_quant_bridge: boolean;
  autonomous_hypothesis: boolean;
  autonomous_loop: boolean;
  /** Always false until a real retrain→deploy consumer exists. */
  autonomous_loop_implemented: boolean;
  /** True once HypothesisRunner is wired in the backend. */
  autonomous_hypothesis_implemented: boolean;
  coupling_effective: boolean;
  compute_profile: "FULL" | "MARKET_ONLY";
  agent_brain?: string;
  regime_brain?: string;
  hypothesis?: {
    armed?: boolean;
    total_ok?: number;
    total_skipped?: number;
    total_error?: number;
    last_id?: string | null;
    last_prompt?: string | null;
    last_status?: string | null;
  };
  macro_provenance?: Record<string, { provenance?: string; reason?: string }>;
}

export const getControlState = () =>
  get<SystemControlState>("/control/state");

export const setOperatingMode = (mode: OperatingModeName) =>
  post<SystemControlState>("/control/mode", { mode });

export const setWorldSimulation = (enabled: boolean) =>
  post<SystemControlState>("/control/world-simulation", { enabled });

export const setWorldBridge = (enabled: boolean) =>
  post<SystemControlState>("/control/world-bridge", { enabled });
