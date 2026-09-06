"use client";

import { useEffect, useRef, useState } from "react";

export type RevealDirection = "up" | "down" | "left" | "right" | "none";

export interface ScrollRevealOptions extends IntersectionObserverInit {
  once?: boolean;
}

/**
 * Lightweight Intersection Observer hook for scroll-triggered reveals.
 * No external animation library required.
 */
export function useScrollReveal<T extends HTMLElement = HTMLDivElement>(
  options: ScrollRevealOptions = {},
) {
  const { once = true, threshold = 0.12, rootMargin = "0px 0px -6% 0px", ...rest } =
    options;
  const ref = useRef<T>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true);
          if (once) observer.disconnect();
        } else if (!once) {
          setVisible(false);
        }
      },
      { threshold, rootMargin, ...rest },
    );

    observer.observe(el);
    return () => observer.disconnect();
  }, [once, threshold, rootMargin, rest.root]);

  return { ref, visible };
}
