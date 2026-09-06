/**
 * ECONITH World :: TITAN topology bridge (50 hubs + 100 proxies).
 *
 * Mirrors `econith.world.sovereign.topology` so the globe can render system-scale
 * nodes and collapse them into Regional Clusters when zoomed out.
 * Pure data — no React / Three.js engine logic here.
 */
export const TITAN_HUB_CODES = [
  "USA", "CHN", "DEU", "JPN", "IND", "GBR", "FRA", "BRA", "VNM", "SAU",
  "CAN", "MEX", "ARG", "CHL", "COL",
  "ITA", "ESP", "NLD", "CHE", "SWE", "POL", "BEL", "AUT", "NOR", "DNK",
  "IRL", "FIN", "PRT", "GRC", "CZE",
  "KOR", "AUS", "IDN", "THA", "MYS", "SGP", "PHL", "TWN", "NZL", "HKG",
  "ARE", "TUR", "ISR", "EGY", "ZAF", "NGA", "RUS", "KAZ", "PAK", "BGD",
] as const;

export const TITAN_PROXY_CODES = [
  "HUN", "ROU", "BGR", "HRV", "SVK", "SVN", "LTU", "LVA", "EST", "LUX",
  "MLT", "CYP", "ISL", "UKR", "SRB", "BIH", "ALB", "MKD", "MDA", "GEO",
  "KHM", "LAO", "MMR", "BRN", "MNG", "NPL", "LKA", "MDV", "FJI", "PNG",
  "MAC", "BTN", "TLS", "VUT", "WSM", "TON", "SLB", "KIR", "MHL", "PLW",
  "FSM", "NRU", "TUV", "ASM", "GUM",
  "PER", "URY", "PRY", "BOL", "ECU", "VEN", "CRI", "PAN", "GTM", "HND",
  "SLV", "NIC", "DOM", "JAM", "TTO", "BRB", "BHS", "BLZ", "GUY", "SUR",
  "QAT", "KWT", "BHR", "OMN", "JOR", "LBN", "IRQ", "IRN", "YEM", "MAR",
  "TUN", "DZA", "LBY", "SDN", "ETH", "KEN", "TZA", "UGA", "GHA", "CIV",
  "SEN", "CMR", "AGO", "MOZ", "ZWE",
  "BLR", "AZE", "ARM", "UZB", "TKM", "TJK", "KGZ", "AFG", "PRK", "CUB",
] as const;

export const TITAN_N_HUBS = TITAN_HUB_CODES.length;
export const TITAN_N_PROXIES = TITAN_PROXY_CODES.length;
export const TITAN_N_NODES = TITAN_N_HUBS + TITAN_N_PROXIES;
export const TITAN_FEATURE_DIM = 113;

/** Aggregation clusters for zoomed-out globe rendering (collapse DOM points). */
export const TITAN_REGIONAL_CLUSTERS: Record<string, readonly string[]> = {
  NorthAmerica: ["USA", "CAN", "MEX"],
  SouthAmerica: ["BRA", "ARG", "CHL", "COL", "PER", "URY", "PRY", "BOL", "ECU", "VEN"],
  WesternEurope: [
    "DEU", "GBR", "FRA", "ITA", "ESP", "NLD", "CHE", "BEL", "AUT", "IRL", "PRT", "LUX",
  ],
  NorthernEurope: ["SWE", "NOR", "DNK", "FIN", "ISL", "EST", "LVA", "LTU"],
  CentralEastEurope: [
    "POL", "CZE", "HUN", "ROU", "BGR", "HRV", "SVK", "SVN", "GRC", "UKR", "SRB",
  ],
  EastAsia: ["CHN", "JPN", "KOR", "TWN", "HKG", "MNG", "MAC", "PRK"],
  SouthAsia: ["IND", "PAK", "BGD", "LKA", "NPL", "MDV", "BTN", "AFG"],
  SEAsia: ["VNM", "IDN", "THA", "MYS", "SGP", "PHL", "KHM", "LAO", "MMR", "BRN", "TLS"],
  Oceania: ["AUS", "NZL", "PNG", "FJI", "VUT", "WSM", "TON", "SLB"],
  MENA: [
    "SAU", "ARE", "TUR", "ISR", "EGY", "QAT", "KWT", "BHR", "OMN", "JOR",
    "LBN", "IRQ", "IRN", "YEM", "MAR", "TUN", "DZA", "LBY",
  ],
  SubSaharanAfrica: [
    "ZAF", "NGA", "ETH", "KEN", "TZA", "UGA", "GHA", "CIV", "SEN", "CMR",
    "AGO", "MOZ", "ZWE", "SDN",
  ],
  Eurasia: ["RUS", "KAZ", "BLR", "AZE", "ARM", "UZB", "TKM", "TJK", "KGZ", "GEO", "MDA"],
  Caribbean: [
    "DOM", "JAM", "TTO", "BRB", "BHS", "CUB", "CRI", "PAN", "GTM", "HND",
    "SLV", "NIC", "BLZ", "GUY", "SUR",
  ],
};

const HUB_SET = new Set<string>(TITAN_HUB_CODES);

export function titanTierOf(code: string): "hub" | "proxy" {
  return HUB_SET.has(code) ? "hub" : "proxy";
}

/** Pick visible nodes for a camera altitude / zoom band. */
export function titanVisibleCodes(zoom: "far" | "mid" | "near"): string[] {
  if (zoom === "far") {
    // One representative per regional cluster (prefer hubs).
    return Object.values(TITAN_REGIONAL_CLUSTERS).map((members) => {
      const hub = members.find((c) => HUB_SET.has(c));
      return hub ?? members[0];
    });
  }
  if (zoom === "mid") return [...TITAN_HUB_CODES];
  return [...TITAN_HUB_CODES, ...TITAN_PROXY_CODES];
}
