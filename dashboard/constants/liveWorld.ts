/** Nations the WorldKernel actually simulates / mutates today. */
export const LIVE_BACKEND_CODES = [
  "USA",
  "CHN",
  "VNM",
  "JPN",
  "IND",
  "DEU",
] as const;

export type LiveBackendCode = (typeof LIVE_BACKEND_CODES)[number];

export const LIVE_BACKEND_SET = new Set<string>(LIVE_BACKEND_CODES);

export function isLiveBackendNation(code: string): boolean {
  return LIVE_BACKEND_SET.has(code.toUpperCase());
}
