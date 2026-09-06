/**
 * ECONITH World :: GeoJSON helpers.
 * Pure functions for reading Natural Earth country features: codes, names,
 * continents, centroids (for fly-to), and reference population / area stats.
 */
import {
  COUNTRY_ALIASES,
  COUNTRY_REFERENCE,
  ISO2_TO_ISO3,
} from "@/constants/countryReference";
import { isSimNation } from "@/constants/simNations";
import { countryName, ISO3_TO_NAME } from "@/constants/macroFeatures";

export interface GeoGeometry {
  type: string;
  coordinates: unknown;
}
export interface GeoFeature {
  type: string;
  properties: Record<string, unknown>;
  geometry: GeoGeometry;
}

const A2_TO_A3: Record<string, string> = {
  US: "USA", CN: "CHN", VN: "VNM", JP: "JPN", IN: "IND", DE: "DEU",
  GB: "GBR", FR: "FRA", RU: "RUS", BR: "BRA", CA: "CAN", AU: "AUS",
};

export function prop(f: GeoFeature, keys: string[]): string {
  for (const k of keys) {
    const v = f.properties?.[k];
    if (typeof v === "string" && v && v !== "-99") return v;
  }
  return "";
}

export function featCode(f: GeoFeature): string {
  const a3 = prop(f, ["ISO_A3", "ADM0_A3", "ISO_A3_EH", "SOV_A3", "iso_a3"]);
  if (a3) {
    const up = a3.toUpperCase();
    if (up === "-99" || up === "UNK") {
      const admin = prop(f, ["ADMIN", "NAME", "NAME_LONG", "name", "admin"]).toLowerCase();
      if (admin.includes("antarct")) return "ATA";
    }
    return up;
  }
  const a2 = prop(f, ["ISO_A2", "ISO_A2_EH", "iso_a2"]);
  return A2_TO_A3[a2?.toUpperCase()] ?? ISO2_TO_ISO3[a2?.toUpperCase()] ?? "";
}

export function featName(f: GeoFeature): string {
  return countryName(
    featCode(f),
    prop(f, ["ADMIN", "NAME", "NAME_LONG", "name", "admin"]),
  );
}

export function featContinent(f: GeoFeature): string {
  return prop(f, ["CONTINENT", "continent", "REGION_UN", "region_un"]) || "Other";
}

/** Iterate every linear ring (outer + holes) of a Polygon/MultiPolygon. */
function eachRing(geom: GeoGeometry, cb: (ring: number[][]) => void): void {
  const coords = geom.coordinates as unknown;
  if (!Array.isArray(coords)) return;
  if (geom.type === "Polygon") {
    for (const ring of coords as number[][][]) cb(ring);
  } else if (geom.type === "MultiPolygon") {
    for (const poly of coords as number[][][][]) for (const ring of poly) cb(ring);
  }
}

export function centroidOf(f: GeoFeature): { lat: number; lng: number } {
  let sx = 0;
  let sy = 0;
  let n = 0;
  eachRing(f.geometry, (ring) => {
    for (const pt of ring) {
      sx += pt[0];
      sy += pt[1];
      n += 1;
    }
  });
  if (n === 0) return { lat: 0, lng: 0 };
  return { lng: sx / n, lat: sy / n };
}

export interface QuickStats {
  name: string;
  gdp: number;
  population: number;
  area: number;
}

/**
 * Resolve a search query to an ISO-3 country code.
 * Prioritises ISO-2/ISO-3 codes and exact names over substring matches
 * (so "US" → USA, not Russia).
 */
export function resolveCountryQuery(query: string): string | null {
  const raw = query.trim();
  if (!raw) return null;

  const upper = raw.toUpperCase();
  const lower = raw.toLowerCase();

  if (ISO2_TO_ISO3[upper]) return ISO2_TO_ISO3[upper];
  if (upper.length === 3 && ISO3_TO_NAME[upper]) return upper;

  if (COUNTRY_ALIASES[lower]) return COUNTRY_ALIASES[lower];

  for (const [code, name] of Object.entries(ISO3_TO_NAME)) {
    if (name.toLowerCase() === lower) return code;
  }

  const startsWith = Object.entries(ISO3_TO_NAME)
    .filter(([, name]) => name.toLowerCase().startsWith(lower))
    .sort((a, b) => a[1].length - b[1].length);
  if (startsWith.length > 0) return startsWith[0][0];

  const wordHits = Object.entries(ISO3_TO_NAME).filter(([, name]) => {
    const nl = name.toLowerCase();
    return (
      nl.split(/\W+/).some((w) => w.startsWith(lower)) ||
      nl.split(/\s+/).some((w) => w === lower)
    );
  });
  if (wordHits.length === 1) return wordHits[0][0];
  if (wordHits.length > 1) {
    wordHits.sort((a, b) => a[1].length - b[1].length);
    return wordHits[0][0];
  }

  return null;
}

/**
 * Population, area and GDP for quick-inspect popups.
 * Simulated nations use live backend values; observed nations use reference data.
 */
export function mockCountryStats(
  f: GeoFeature,
  code: string,
  real?: { gdp?: number; population?: number },
): QuickStats {
  const name = featName(f);
  const ref = COUNTRY_REFERENCE[code];
  const area = ref?.area ?? 0;
  const refPop = ref?.population ?? 0;

  if (real && isSimNation(code)) {
    return {
      name,
      gdp: real.gdp ?? refPop * 8_000,
      population: real.population ?? refPop,
      area,
    };
  }

  if (ref) {
    const perCapita = refPop < 10_000 ? 50_000 : 8_000;
    return { name, gdp: refPop * perCapita, population: refPop, area };
  }

  return { name, gdp: 0, population: 0, area: 0 };
}
