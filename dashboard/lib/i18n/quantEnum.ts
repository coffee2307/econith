import type { TranslateFn } from "./translate";

export type QuantEnumGroup =
  | "action"
  | "verdict"
  | "side"
  | "quantMode"
  | "execRouting"
  | "breaker"
  | "sentinelMode"
  | "vendorStatus";

/** Translate a backend enum/token; falls back to raw value if no dictionary entry. */
export function tQuantEnum(
  t: TranslateFn,
  group: QuantEnumGroup,
  value?: string | null,
): string {
  if (value == null || value === "" || value === "—") return value ?? "—";
  const path = `quant.enums.${group}.${value}`;
  const translated = t(path);
  return translated === path ? value : translated;
}
