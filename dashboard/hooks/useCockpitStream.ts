"use client";

import { useEffect, useRef, useState } from "react";
import type {
  CockpitSocketMessage,
  ICockpitTelemetryFrame,
} from "@/lib/cockpit/types";

export type CockpitConnectionStatus =
  | "connecting"
  | "open"
  | "reconnecting"
  | "closed";

const DEFAULT_COCKPIT_WS =
  process.env.NEXT_PUBLIC_COCKPIT_WS_URL ??
  (process.env.NEXT_PUBLIC_WS_URL
    ? process.env.NEXT_PUBLIC_WS_URL.replace("/stream/metrics", "/stream/cockpit")
    : "ws://localhost:8000/api/v1/stream/cockpit");

export interface CockpitStream {
  frame: ICockpitTelemetryFrame | null;
  status: CockpitConnectionStatus;
  attempts: number;
}

export function useCockpitStream(url = DEFAULT_COCKPIT_WS): CockpitStream {
  const [frame, setFrame] = useState<ICockpitTelemetryFrame | null>(null);
  const [status, setStatus] = useState<CockpitConnectionStatus>("connecting");
  const [attempts, setAttempts] = useState(0);
  const attemptRef = useRef(0);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let stopped = false;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

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
          const msg = JSON.parse(event.data as string) as CockpitSocketMessage;
          if (msg.type === "frame") {
            setFrame(msg.frame);
          }
        } catch {
          // ignore malformed frame
        }
      };

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
      const delay = Math.min(10_000, 500 * 2 ** attemptRef.current) + Math.random() * 250;
      attemptRef.current += 1;
      setAttempts(attemptRef.current);
      timer = setTimeout(open, delay);
    };

    open();

    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      if (ws) {
        ws.onclose = null;
        ws.onerror = null;
        ws.onmessage = null;
        try {
          ws.close();
        } catch {
          // noop
        }
      }
    };
  }, [url]);

  return { frame, status, attempts };
}
