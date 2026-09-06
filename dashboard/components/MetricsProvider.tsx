"use client";

/**
 * ECONITH :: MetricsProvider
 *
 * App-wide context that hosts a single live metrics WebSocket connection
 * (via useMetricsStream) and shares it with every page/component through the
 * `useMetrics()` hook. Mounted once in the root layout so navigation between
 * /quant and /world reuses the same connection.
 */
import { createContext, useContext } from "react";
import {
  type MetricsStream,
  useMetricsStream,
} from "@/hooks/useMetricsStream";

const MetricsContext = createContext<MetricsStream | null>(null);

export function MetricsProvider({ children }: { children: React.ReactNode }) {
  const stream = useMetricsStream();
  return (
    <MetricsContext.Provider value={stream}>
      {children}
    </MetricsContext.Provider>
  );
}

export function useMetrics(): MetricsStream {
  const ctx = useContext(MetricsContext);
  if (ctx === null) {
    throw new Error("useMetrics must be used within a <MetricsProvider>");
  }
  return ctx;
}
