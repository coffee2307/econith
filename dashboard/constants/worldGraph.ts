/**
 * ECONITH World :: 50-Node Interconnected Global Economy — topology.
 *
 * Hub & Spoke (Core vs. Proxy) state architecture:
 *
 *   Tier 1 — Core Hubs (10):   run the full 3-agent loop, compute 111 features
 *                              independently, and BROADCAST macro shocks to their
 *                              dependent proxies.
 *   Tier 2 — Proxy Nodes (40): run a lighter derivative/correlation engine, their
 *                              baseline drifting toward a weighted blend of their
 *                              assigned hubs — unless the user manually overrides a
 *                              feature, which then takes precedence (localized shock).
 *
 * This module is pure topology + seed data: no React, no engine logic.
 */
import type { CountryVectors, MacroVector } from "@/hooks/useMetricsStream";

export type NodeTier = "hub" | "proxy";

export interface HubWeight {
  hub: string;   // ISO-3 of a Core Hub
  weight: number;
}

// ---------------------------------------------------------------------------
// Tier 1 — the 10 Core Hubs (exact array, no placeholders).
// ---------------------------------------------------------------------------
export const HUB_CODES = [
  "USA", "CHN", "DEU", "JPN", "IND", "GBR", "FRA", "BRA", "VNM", "SAU",
] as const;

export type HubCode = (typeof HUB_CODES)[number];

// ---------------------------------------------------------------------------
// Tier 2 — the 40 Proxy Nodes, grouped with their hub dependencies (exact arrays).
// Weights are normalised at load; they only need to be proportional.
// ---------------------------------------------------------------------------
export const PROXY_GROUPS: Record<string, string[]> = {
  Europe: ["ITA", "ESP", "NLD", "CHE", "SWE", "POL", "BEL", "AUT", "NOR", "DNK"],
  APAC: ["KOR", "AUS", "IDN", "THA", "MYS", "SGP", "PHL", "TWN", "PAK", "BGD"],
  Americas: ["CAN", "MEX", "ARG", "COL", "CHL", "PER"],
  MENA_Africa: ["ARE", "TUR", "EGY", "NGA", "ZAF", "ISR", "IRN", "QAT"],
  Special: ["RUS", "UKR", "KAZ", "GRC", "HUN", "CZE"],
};

// Per-proxy hub dependency vectors (which hubs it correlates to, and how much).
export const PROXY_DEPENDENCIES: Record<string, HubWeight[]> = {
  // --- Europe (proxy to DEU / GBR / FRA) ---
  ITA: [{ hub: "DEU", weight: 0.45 }, { hub: "FRA", weight: 0.4 }, { hub: "GBR", weight: 0.15 }],
  ESP: [{ hub: "FRA", weight: 0.45 }, { hub: "DEU", weight: 0.4 }, { hub: "GBR", weight: 0.15 }],
  NLD: [{ hub: "DEU", weight: 0.6 }, { hub: "GBR", weight: 0.25 }, { hub: "FRA", weight: 0.15 }],
  CHE: [{ hub: "DEU", weight: 0.55 }, { hub: "FRA", weight: 0.3 }, { hub: "GBR", weight: 0.15 }],
  SWE: [{ hub: "DEU", weight: 0.5 }, { hub: "GBR", weight: 0.35 }, { hub: "FRA", weight: 0.15 }],
  POL: [{ hub: "DEU", weight: 0.65 }, { hub: "FRA", weight: 0.2 }, { hub: "GBR", weight: 0.15 }],
  BEL: [{ hub: "DEU", weight: 0.45 }, { hub: "FRA", weight: 0.4 }, { hub: "GBR", weight: 0.15 }],
  AUT: [{ hub: "DEU", weight: 0.7 }, { hub: "FRA", weight: 0.2 }, { hub: "GBR", weight: 0.1 }],
  NOR: [{ hub: "GBR", weight: 0.5 }, { hub: "DEU", weight: 0.4 }, { hub: "FRA", weight: 0.1 }],
  DNK: [{ hub: "DEU", weight: 0.55 }, { hub: "GBR", weight: 0.3 }, { hub: "FRA", weight: 0.15 }],

  // --- APAC (proxy to CHN / JPN / VNM) ---
  KOR: [{ hub: "CHN", weight: 0.45 }, { hub: "JPN", weight: 0.4 }, { hub: "VNM", weight: 0.15 }],
  AUS: [{ hub: "CHN", weight: 0.6 }, { hub: "JPN", weight: 0.25 }, { hub: "VNM", weight: 0.15 }],
  IDN: [{ hub: "CHN", weight: 0.5 }, { hub: "VNM", weight: 0.3 }, { hub: "JPN", weight: 0.2 }],
  THA: [{ hub: "CHN", weight: 0.45 }, { hub: "VNM", weight: 0.35 }, { hub: "JPN", weight: 0.2 }],
  MYS: [{ hub: "CHN", weight: 0.5 }, { hub: "VNM", weight: 0.3 }, { hub: "JPN", weight: 0.2 }],
  SGP: [{ hub: "CHN", weight: 0.45 }, { hub: "JPN", weight: 0.3 }, { hub: "VNM", weight: 0.25 }],
  PHL: [{ hub: "CHN", weight: 0.45 }, { hub: "JPN", weight: 0.3 }, { hub: "VNM", weight: 0.25 }],
  TWN: [{ hub: "CHN", weight: 0.5 }, { hub: "JPN", weight: 0.35 }, { hub: "VNM", weight: 0.15 }],
  PAK: [{ hub: "CHN", weight: 0.7 }, { hub: "VNM", weight: 0.15 }, { hub: "JPN", weight: 0.15 }],
  BGD: [{ hub: "CHN", weight: 0.5 }, { hub: "VNM", weight: 0.35 }, { hub: "JPN", weight: 0.15 }],

  // --- Americas (proxy to USA / BRA) ---
  CAN: [{ hub: "USA", weight: 0.85 }, { hub: "BRA", weight: 0.15 }],
  MEX: [{ hub: "USA", weight: 0.8 }, { hub: "BRA", weight: 0.2 }],
  ARG: [{ hub: "BRA", weight: 0.6 }, { hub: "USA", weight: 0.4 }],
  COL: [{ hub: "USA", weight: 0.55 }, { hub: "BRA", weight: 0.45 }],
  CHL: [{ hub: "USA", weight: 0.5 }, { hub: "BRA", weight: 0.5 }],
  PER: [{ hub: "BRA", weight: 0.55 }, { hub: "USA", weight: 0.45 }],

  // --- MENA / Africa (proxy to SAU, with a light USA oil-trade linkage) ---
  ARE: [{ hub: "SAU", weight: 0.8 }, { hub: "USA", weight: 0.2 }],
  TUR: [{ hub: "SAU", weight: 0.5 }, { hub: "DEU", weight: 0.5 }],
  EGY: [{ hub: "SAU", weight: 0.7 }, { hub: "USA", weight: 0.3 }],
  NGA: [{ hub: "SAU", weight: 0.6 }, { hub: "GBR", weight: 0.4 }],
  ZAF: [{ hub: "SAU", weight: 0.4 }, { hub: "CHN", weight: 0.6 }],
  ISR: [{ hub: "USA", weight: 0.6 }, { hub: "SAU", weight: 0.4 }],
  IRN: [{ hub: "SAU", weight: 0.6 }, { hub: "CHN", weight: 0.4 }],
  QAT: [{ hub: "SAU", weight: 0.75 }, { hub: "USA", weight: 0.25 }],

  // --- Special / Others (mapped to hubs only, never proxy->proxy) ---
  RUS: [{ hub: "CHN", weight: 0.5 }, { hub: "SAU", weight: 0.3 }, { hub: "DEU", weight: 0.2 }],
  UKR: [{ hub: "DEU", weight: 0.5 }, { hub: "USA", weight: 0.5 }],
  KAZ: [{ hub: "CHN", weight: 0.5 }, { hub: "SAU", weight: 0.3 }, { hub: "DEU", weight: 0.2 }],
  GRC: [{ hub: "DEU", weight: 0.5 }, { hub: "FRA", weight: 0.3 }, { hub: "GBR", weight: 0.2 }],
  HUN: [{ hub: "DEU", weight: 0.6 }, { hub: "FRA", weight: 0.25 }, { hub: "GBR", weight: 0.15 }],
  CZE: [{ hub: "DEU", weight: 0.7 }, { hub: "FRA", weight: 0.2 }, { hub: "GBR", weight: 0.1 }],
};

export const PROXY_CODES = Object.values(PROXY_GROUPS).flat();
export const ALL_NATION_CODES = [...HUB_CODES, ...PROXY_CODES];

const HUB_SET = new Set<string>(HUB_CODES);
export function tierOf(code: string): NodeTier {
  return HUB_SET.has(code) ? "hub" : "proxy";
}
export function isHub(code: string): boolean {
  return HUB_SET.has(code);
}

/** Normalised hub dependencies for a proxy (weights sum to 1). */
export function dependenciesFor(code: string): HubWeight[] {
  const raw = PROXY_DEPENDENCIES[code];
  if (raw && raw.length) {
    const total = raw.reduce((s, d) => s + d.weight, 0) || 1;
    return raw.map((d) => ({ hub: d.hub, weight: d.weight / total }));
  }
  // Lazily-added / unknown proxies fall back to a continent-nearest hub.
  const hub = FALLBACK_HUB_BY_CONTINENT[CODE_CONTINENT[code] ?? "Europe"] ?? "USA";
  return [{ hub, weight: 1 }];
}

// ---------------------------------------------------------------------------
// Continent assignment (drives globe colour + a fallback hub for stray clicks).
// ---------------------------------------------------------------------------
export const CODE_CONTINENT: Record<string, string> = {
  USA: "North America", CAN: "North America", MEX: "North America",
  BRA: "South America", ARG: "South America", COL: "South America",
  CHL: "South America", PER: "South America",
  DEU: "Europe", GBR: "Europe", FRA: "Europe", ITA: "Europe", ESP: "Europe",
  NLD: "Europe", CHE: "Europe", SWE: "Europe", POL: "Europe", BEL: "Europe",
  AUT: "Europe", NOR: "Europe", DNK: "Europe", GRC: "Europe", HUN: "Europe",
  CZE: "Europe", UKR: "Europe", RUS: "Europe",
  CHN: "Asia", JPN: "Asia", IND: "Asia", VNM: "Asia", KOR: "Asia", IDN: "Asia",
  THA: "Asia", MYS: "Asia", SGP: "Asia", PHL: "Asia", TWN: "Asia", PAK: "Asia",
  BGD: "Asia", KAZ: "Asia", IRN: "Asia", ISR: "Asia", TUR: "Asia",
  SAU: "Asia", ARE: "Asia", QAT: "Asia",
  AUS: "Oceania",
  EGY: "Africa", NGA: "Africa", ZAF: "Africa",
};

const FALLBACK_HUB_BY_CONTINENT: Record<string, string> = {
  "North America": "USA",
  "South America": "BRA",
  Europe: "DEU",
  Asia: "CHN",
  Oceania: "CHN",
  Africa: "SAU",
};

// ---------------------------------------------------------------------------
// Seed defaults — mirror the backend Pydantic vector defaults so every one of
// the 111 features has a real baseline value on ALL 50 nations.
// ---------------------------------------------------------------------------
export const DEFAULT_MONETARY: MacroVector = {
  interest_rate: 0.04, inflation_target: 0.02, inflation_cpi: 0.03,
  inflation_ppi: 0.028, core_inflation: 0.025, money_supply_m1: 1.0e12,
  money_supply_m2: 4.0e12, money_supply_m3: 6.0e12, m2_growth: 0.05,
  velocity_of_money: 1.4, central_bank_reserves: 5.0e11, reserve_requirement: 0.10,
  discount_rate: 0.045, qe_intensity: 0.0, credit_growth: 0.06,
  real_interest_rate: 0.01, yield_2y: 0.04, yield_10y: 0.045, fx_spot: 1.0,
  fx_forward_12m: 1.0,
};
export const DEFAULT_FISCAL: MacroVector = {
  corporate_tax: 0.21, individual_tax: 0.30, vat: 0.10, capital_gains_tax: 0.15,
  govt_debt_to_gdp: 0.90, budget_deficit_pct: 0.04, govt_spending_pct: 0.38,
  trade_balance_pct: -0.02, current_account_pct: -0.02, export_index: 100.0,
  import_index: 100.0, fdi_inflow: 2.0e11, fdi_outflow: 1.5e11,
  foreign_reserves: 5.0e11, sovereign_rating: 0.85, avg_import_tariff: 0.03,
  avg_export_subsidy: 0.01, customs_revenue_pct: 0.01, public_investment_pct: 0.04,
  tax_revenue_pct: 0.25, informal_economy_pct: 0.12, ease_of_business: 0.75,
  property_tax: 0.01, healthcare_budget_pct: 0.08, education_budget_pct: 0.05,
};
export const DEFAULT_LABOR: MacroVector = {
  population: 100_000_000, workforce_participation: 0.62, unemployment: 0.05,
  underemployment: 0.07, wage_growth: 0.03, productivity_index: 100.0,
  dependency_ratio: 0.55, birth_rate: 0.012, death_rate: 0.008, net_migration: 0.001,
  median_age: 38.0, urbanization: 0.70, gini_coefficient: 0.38, literacy_rate: 0.95,
  tertiary_education: 0.45, healthcare_index: 0.75, labor_cost_index: 100.0,
  union_density: 0.15, youth_unemployment: 0.12, retirement_age: 65.0,
  minimum_wage: 7.25, immigration_quota: 0.002,
};
export const DEFAULT_INDUSTRIAL: MacroVector = {
  manufacturing_pmi: 51.0, services_pmi: 52.0, industrial_production: 100.0,
  capacity_utilization: 0.78, energy_production: 100.0, energy_consumption: 100.0,
  energy_independence: 0.80, oil_reserves: 5.0e9, gas_reserves: 2.0e12,
  coal_reserves: 1.0e10, rare_earth_reserves: 1.0e6, semiconductor_capacity: 0.10,
  agricultural_output: 100.0, food_security_index: 0.80, water_stress_index: 0.30,
  supply_chain_friction: 0.20, logistics_performance: 0.75, rnd_spending_pct: 0.025,
  patents_index: 100.0, infrastructure_index: 0.75, carbon_intensity: 0.40,
  renewable_share: 0.25, clean_energy_subsidy: 0.02, tech_export_controls: 0.10,
  corporate_rnd_subsidy: 0.015,
};
export const DEFAULT_GEO: MacroVector = {
  defense_spending_pct: 0.025, military_strength_index: 0.50, political_stability: 0.70,
  corruption_index: 0.55, press_freedom: 0.65, consumer_confidence: 0.60,
  business_confidence: 0.60, social_unrest_index: 0.20, cyber_capability: 0.50,
  soft_power_index: 0.55, diplomatic_reach: 0.50, sanctions_exposure: 0.10,
  energy_security: 0.70, nuclear_capability: 0.0, alliance_cohesion: 0.60,
  public_approval: 0.50, election_risk: 0.20, geopolitical_risk: 0.25,
};

export function defaultVectors(): CountryVectors {
  return {
    monetary: { ...DEFAULT_MONETARY },
    fiscal: { ...DEFAULT_FISCAL },
    labor: { ...DEFAULT_LABOR },
    industrial: { ...DEFAULT_INDUSTRIAL },
    geopolitical: { ...DEFAULT_GEO },
  };
}

// ---------------------------------------------------------------------------
// Curated economic profiles for the 10 Core Hubs (deltas over defaults).
// Values are in native/backend units (fractions as decimals).
// ---------------------------------------------------------------------------
export interface CountryProfile {
  name: string;
  gdp: number;
  gdp_growth: number;
  monetary?: Partial<MacroVector>;
  fiscal?: Partial<MacroVector>;
  labor?: Partial<MacroVector>;
  industrial?: Partial<MacroVector>;
  geopolitical?: Partial<MacroVector>;
}

export const HUB_PROFILES: Record<HubCode, CountryProfile> = {
  USA: {
    name: "United States", gdp: 27.4e12, gdp_growth: 0.025,
    monetary: { interest_rate: 0.0525, inflation_cpi: 0.032, fx_spot: 1.0 },
    fiscal: { corporate_tax: 0.21, govt_debt_to_gdp: 1.22, trade_balance_pct: -0.035 },
    industrial: { semiconductor_capacity: 0.12, manufacturing_pmi: 49.5 },
    geopolitical: { military_strength_index: 0.95, political_stability: 0.62, nuclear_capability: 1.0 },
  },
  CHN: {
    name: "China", gdp: 17.8e12, gdp_growth: 0.048,
    monetary: { interest_rate: 0.032, inflation_cpi: 0.018, fx_spot: 7.2 },
    fiscal: { corporate_tax: 0.25, govt_debt_to_gdp: 0.83, trade_balance_pct: 0.035, export_index: 130 },
    industrial: { semiconductor_capacity: 0.16, manufacturing_pmi: 50.8, rare_earth_reserves: 4.4e7 },
    geopolitical: { military_strength_index: 0.80, political_stability: 0.68, nuclear_capability: 1.0 },
  },
  DEU: {
    name: "Germany", gdp: 4.5e12, gdp_growth: 0.006,
    monetary: { interest_rate: 0.040, inflation_cpi: 0.029, fx_spot: 0.92 },
    fiscal: { corporate_tax: 0.30, govt_debt_to_gdp: 0.64, trade_balance_pct: 0.05 },
    industrial: { manufacturing_pmi: 47.0, renewable_share: 0.46 },
    geopolitical: { political_stability: 0.80 },
  },
  JPN: {
    name: "Japan", gdp: 4.2e12, gdp_growth: 0.011,
    monetary: { interest_rate: 0.005, inflation_cpi: 0.026, fx_spot: 150 },
    fiscal: { corporate_tax: 0.297, govt_debt_to_gdp: 2.60 },
    industrial: { semiconductor_capacity: 0.10, manufacturing_pmi: 49.0 },
    labor: { median_age: 49.0, unemployment: 0.026 },
    geopolitical: { political_stability: 0.78 },
  },
  IND: {
    name: "India", gdp: 3.7e12, gdp_growth: 0.068,
    monetary: { interest_rate: 0.065, inflation_cpi: 0.051, fx_spot: 83 },
    fiscal: { corporate_tax: 0.252, govt_debt_to_gdp: 0.82 },
    industrial: { manufacturing_pmi: 56.0 },
    labor: { median_age: 28.0, workforce_participation: 0.55 },
    geopolitical: { nuclear_capability: 1.0 },
  },
  GBR: {
    name: "United Kingdom", gdp: 3.3e12, gdp_growth: 0.011,
    monetary: { interest_rate: 0.0475, inflation_cpi: 0.030, fx_spot: 0.79 },
    fiscal: { corporate_tax: 0.25, govt_debt_to_gdp: 1.00 },
    industrial: { manufacturing_pmi: 48.0 },
    geopolitical: { military_strength_index: 0.72, political_stability: 0.70, nuclear_capability: 1.0 },
  },
  FRA: {
    name: "France", gdp: 3.1e12, gdp_growth: 0.009,
    monetary: { interest_rate: 0.040, inflation_cpi: 0.027, fx_spot: 0.92 },
    fiscal: { corporate_tax: 0.25, govt_debt_to_gdp: 1.11 },
    industrial: { manufacturing_pmi: 47.5, renewable_share: 0.27 },
    geopolitical: { military_strength_index: 0.70, political_stability: 0.66, nuclear_capability: 1.0 },
  },
  BRA: {
    name: "Brazil", gdp: 2.1e12, gdp_growth: 0.029,
    monetary: { interest_rate: 0.1075, inflation_cpi: 0.045, fx_spot: 5.0 },
    fiscal: { corporate_tax: 0.34, govt_debt_to_gdp: 0.74 },
    industrial: { agricultural_output: 160, renewable_share: 0.47 },
    geopolitical: { political_stability: 0.55 },
  },
  VNM: {
    name: "Vietnam", gdp: 0.43e12, gdp_growth: 0.062,
    monetary: { interest_rate: 0.045, inflation_cpi: 0.034, fx_spot: 24_500 },
    fiscal: { corporate_tax: 0.20, govt_debt_to_gdp: 0.37, trade_balance_pct: 0.02, export_index: 120 },
    industrial: { manufacturing_pmi: 51.5, semiconductor_capacity: 0.01 },
    labor: { workforce_participation: 0.74, wage_growth: 0.06 },
    geopolitical: { political_stability: 0.66 },
  },
  SAU: {
    name: "Saudi Arabia", gdp: 1.1e12, gdp_growth: 0.031,
    monetary: { interest_rate: 0.055, inflation_cpi: 0.023, fx_spot: 3.75 },
    fiscal: { corporate_tax: 0.20, govt_debt_to_gdp: 0.24, trade_balance_pct: 0.10 },
    industrial: { oil_reserves: 2.6e11, energy_independence: 1.8, energy_production: 260 },
    geopolitical: { energy_security: 0.95, military_strength_index: 0.55 },
  },
};

// Nations that capture diverted supply chains during a tariff war (alt manufacturing).
export const DIVERSION_BENEFICIARIES = ["VNM", "MEX", "IND", "IDN", "THA", "MYS", "BGD"];

// Fields the proxy correlation engine tracks against its hubs.
export const CORRELATED_FIELDS: { group: keyof CountryVectors | "top"; field: string }[] = [
  { group: "monetary", field: "interest_rate" },
  { group: "monetary", field: "inflation_cpi" },
  { group: "monetary", field: "yield_10y" },
  { group: "top", field: "gdp_growth" },
  { group: "fiscal", field: "export_index" },
  { group: "fiscal", field: "trade_balance_pct" },
  { group: "fiscal", field: "individual_tax" },
  { group: "fiscal", field: "vat" },
  { group: "fiscal", field: "corporate_tax" },
  { group: "industrial", field: "manufacturing_pmi" },
  { group: "geopolitical", field: "business_confidence" },
  { group: "geopolitical", field: "consumer_confidence" },
  { group: "geopolitical", field: "social_unrest_index" },
];
