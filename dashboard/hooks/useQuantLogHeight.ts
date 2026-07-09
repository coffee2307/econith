"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "econith-quant-log-h";
const DEFAULT = 152;
const MIN = 88;
const MAX = 520;

function clamp(n: number): number {
  return Math.min(MAX, Math.max(MIN, n));
}

/** Persisted height (px) for the Quant event-log dock. Drag up = taller log. */
export function useQuantLogHeight() {
  const [height, setHeight] = useState(DEFAULT);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (!saved) return;
      const n = parseInt(saved, 10);
      if (Number.isFinite(n)) setHeight(clamp(n));
    } catch {
      // ignore
    }
  }, []);

  const adjust = useCallback((dy: number) => {
    setHeight((h) => {
      const next = clamp(h - dy);
      try {
        localStorage.setItem(STORAGE_KEY, String(next));
      } catch {
        // ignore
      }
      return next;
    });
  }, []);

  return { height, adjust, min: MIN, max: MAX };
}
