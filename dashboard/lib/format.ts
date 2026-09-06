/** ECONITH :: display formatting helpers (null-safe). */

export function fmtUsd(value: number | null | undefined, dp = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString("en-US", {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function fmtNum(value: number | null | undefined, dp = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(dp);
}

export function fmtPct(value: number | null | undefined, dp = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(dp)}%`;
}

export function fmtSigned(value: number | null | undefined, dp = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const s = value.toFixed(dp);
  return value > 0 ? `+${s}` : s;
}

export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "--:--:--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--:--";
  return d.toLocaleTimeString("en-GB", { hour12: false });
}
