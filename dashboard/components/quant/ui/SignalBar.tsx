"use client";

/**
 * ECONITH :: SignalBar
 *
 * Bipolar bias/sentiment bar centered at 0. Long (>0) fills right in the long
 * color, short (<0) fills left in the short color. Used by the debate council
 * and alpha candidate. `value` is expected in roughly [-1, 1].
 */
export function SignalBar({ value, height = 6 }: { value: number; height?: number }) {
  const v = Math.max(-1, Math.min(1, Number.isFinite(value) ? value : 0));
  const pct = Math.abs(v) * 50;
  const color = v > 0.02 ? "var(--wr-long)" : v < -0.02 ? "var(--wr-short)" : "var(--wr-flat)";

  return (
    <div className="wr-bias-track" style={{ height }}>
      <div className="wr-bias-mid" />
      <div
        className="wr-bias-fill"
        style={{
          left: v >= 0 ? "50%" : `${50 - pct}%`,
          width: `${pct}%`,
          backgroundColor: color,
        }}
      />
    </div>
  );
}
