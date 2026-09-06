"use client";

import type { ReactNode } from "react";
import {
  useScrollReveal,
  type RevealDirection,
  type ScrollRevealOptions,
} from "@/hooks/useScrollReveal";

const DIRECTION_CLASS: Record<RevealDirection, string> = {
  up: "scroll-reveal-from-up",
  down: "scroll-reveal-from-down",
  left: "scroll-reveal-from-left",
  right: "scroll-reveal-from-right",
  none: "scroll-reveal-from-none",
};

export function ScrollReveal({
  children,
  className = "",
  delay = 0,
  direction = "up",
  options,
}: {
  children: ReactNode;
  className?: string;
  delay?: number;
  direction?: RevealDirection;
  options?: ScrollRevealOptions;
}) {
  const { ref, visible } = useScrollReveal<HTMLDivElement>(options);

  return (
    <div
      ref={ref}
      className={[
        "scroll-reveal",
        DIRECTION_CLASS[direction],
        visible ? "scroll-reveal-visible" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ transitionDelay: `${delay}ms` }}
    >
      {children}
    </div>
  );
}
