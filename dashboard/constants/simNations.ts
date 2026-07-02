import { ALL_NATION_CODES } from "@/constants/worldGraph";

const SIM_SET = new Set<string>(ALL_NATION_CODES);

/** True for the 50 Hub/Proxy nations that run the world simulation. */
export function isSimNation(code: string): boolean {
  return SIM_SET.has(code);
}

/** Territories with reference stats only — never spawn sim nodes. */
export const REFERENCE_ONLY_CODES = new Set([
  "ATA", // Antarctica
  "ATF", // French Southern Territories
  "BVT", // Bouvet Island
  "HMD", // Heard Island
  "IOT", // British Indian Ocean
  "SGS", // South Georgia
  "UMI", // US Minor Outlying Islands
]);

export function isReferenceOnly(code: string): boolean {
  return REFERENCE_ONLY_CODES.has(code);
}
