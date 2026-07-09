"use client";

/**
 * ECONITH :: MetricGauge
 *
 * Minimalist SVG arc gauge for single-value risk/health metrics (drawdown,
 * VaR, margin, confidence). Calm palette — a single track + a single value arc,
 * no "christmas tree". `fraction` is the 0..1 fill; `tone` colors the arc.
 * Threshold breaches can flip the tone via the caller.
 */
const TONE: Record<string, string> = {
  ok: "var(--wr-long)",
  long: "var(--wr-long)",
  warn: "#f59e0b",
  danger: "var(--wr-short)",
  accent: "#7c93ff",
  neutral: "var(--color-muted)",
};

const R = 34;
const CX = 42;
const CY = 42;
// 270° sweep starting from lower-left (135°) going clockwise.
const START = 135;
const SWEEP = 270;
const CIRC = 2 * Math.PI * R;
const ARC_LEN = (SWEEP / 360) * CIRC;

function polar(angleDeg: number): [number, number] {
  const a = (angleDeg * Math.PI) / 180;
  return [CX + R * Math.cos(a), CY + R * Math.sin(a)];
}

export function MetricGauge({
  label,
  value,
  fraction,
  tone = "accent",
  sub,
}: {
  label: string;
  value: string;
  fraction: number;
  tone?: keyof typeof TONE | string;
  sub?: string;
}) {
  const f = Math.max(0, Math.min(1, Number.isFinite(fraction) ? fraction : 0));
  const color = TONE[tone] ?? TONE.accent;
  const [sx, sy] = polar(START);
  const [ex, ey] = polar(START + SWEEP);
  const large = SWEEP > 180 ? 1 : 0;
  const trackPath = `M ${sx} ${sy} A ${R} ${R} 0 ${large} 1 ${ex} ${ey}`;

  return (
    <div className="flex flex-col items-center">
      <div className="relative">
        <svg viewBox="0 0 84 84" className="h-[84px] w-[84px]">
          <path
            d={trackPath}
            fill="none"
            stroke="color-mix(in srgb, var(--color-faint) 28%, transparent)"
            strokeWidth={5}
            strokeLinecap="round"
          />
          <path
            d={trackPath}
            fill="none"
            stroke={color}
            strokeWidth={5}
            strokeLinecap="round"
            strokeDasharray={`${ARC_LEN * f} ${CIRC}`}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono text-sm font-bold tabular-nums" style={{ color }}>
            {value}
          </span>
          {sub ? <span className="mt-0.5 text-[8px] text-faint">{sub}</span> : null}
        </div>
      </div>
      <span className="wr-label mt-1 text-center">{label}</span>
    </div>
  );
}
