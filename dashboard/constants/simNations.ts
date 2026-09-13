import { TITAN_HUB_CODES, TITAN_PROXY_CODES } from "@/constants/titanWorld";

const SIM_SET = new Set<string>([...TITAN_HUB_CODES, ...TITAN_PROXY_CODES]);

/** True for the 150 Hub/Proxy nations that run the world simulation. */
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
