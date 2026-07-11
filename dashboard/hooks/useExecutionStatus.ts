"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/api";

export type ExecutionRouting = "LIVE" | "SYNTHETIC" | "DEGRADED" | "OFFLINE";

export interface ExecutionStatus {
  quant_mode: string;
  execution_routing: ExecutionRouting;
  execution_env: "demo" | "live";
  exchange_live: boolean;
  credentialed: boolean;
  testnet: boolean;
  detail: string;
}

export interface ExecutionStatusState {
  execution: ExecutionStatus | null;
  loading: boolean;
}

const POLL_INTERVAL_MS = 8_000;

const OFFLINE_STATUS: ExecutionStatus = {
  quant_mode: "UNKNOWN",
  execution_routing: "OFFLINE",
  execution_env: "demo",
  exchange_live: false,
  credentialed: false,
  testnet: false,
  detail: "Backend unreachable",
};

export function useExecutionStatus(): ExecutionStatusState {
  const [execution, setExecution] = useState<ExecutionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(5_000) });
        if (!res.ok) throw new Error(`${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          const exec = data?.execution as ExecutionStatus | undefined;
          setExecution(exec ?? OFFLINE_STATUS);
          setLoading(false);
        }
      } catch {
        if (!cancelled) {
          setExecution(OFFLINE_STATUS);
          setLoading(false);
        }
      }
    };

    poll();
    timer.current = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  return { execution, loading };
}
