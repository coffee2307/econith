"use client";

/**
 * ECONITH :: StatusBadge
 *
 * Compact status chip used across the war-room top bar. Semantic tone drives
 * border/fill/text via a single CSS custom property (`--wr-chip-color`) so the
 * chip stays flat and theme-aware. Optional live dot + rich tooltip.
 */
import { useState, type ReactNode } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";

export type BadgeTone = "ok" | "warn" | "danger" | "accent" | "neutral" | "long" | "short";

const TONE_COLOR: Record<BadgeTone, string> = {
  ok: "var(--wr-long)",
  long: "var(--wr-long)",
  warn: "#f59e0b",
  danger: "var(--wr-short)",
  short: "var(--wr-short)",
  accent: "#7c93ff",
  neutral: "var(--color-faint)",
};

export function StatusBadge({
  label,
  value,
  tone = "neutral",
  icon,
  live = false,
  suffix,
  tooltip,
}: {
  label: string;
  value: ReactNode;
  tone?: BadgeTone;
  icon?: IconDefinition;
  live?: boolean;
  suffix?: string;
  tooltip?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const color = TONE_COLOR[tone];

  return (
    <div
      className="relative"
      onMouseEnter={() => tooltip && setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <div
        className="flex flex-col rounded-md border border-line bg-elevated/70 px-2.5 py-1"
        style={{ borderColor: `color-mix(in srgb, ${color} 32%, var(--color-line))` }}
      >
        <span className="wr-label leading-none">{label}</span>
        <span
          className="mt-0.5 flex items-center gap-1.5 font-mono text-xs font-bold leading-none"
          style={{ color }}
        >
          {live ? <span className="wr-chip-dot" style={{ color }} /> : null}
          {icon ? <FontAwesomeIcon icon={icon} className="h-2.5 w-2.5" /> : null}
          <span className="whitespace-nowrap">{value}</span>
          {suffix ? <span className="text-faint">{suffix}</span> : null}
        </span>
      </div>
      {open && tooltip ? (
        <div className="absolute right-0 top-full z-50 mt-1.5 w-60 rounded-lg border border-line bg-surface p-2.5 font-mono text-[10px] leading-relaxed text-muted shadow-2xl">
          {tooltip}
        </div>
      ) : null}
    </div>
  );
}
