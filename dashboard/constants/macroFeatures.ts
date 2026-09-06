/**
 * ECONITH World :: macro feature configuration & country metadata.
 *
 * `MACRO_FEATURES` is the single source of truth for every adjustable
 * macroeconomic variable. The UI maps over it to render controls dynamically --
 * there are NO hardcoded input fields anywhere in the component.
 *
 * Every feature points at a real backend field (group/field), so every control
 * actually mutates the simulation via POST /world/country/{code}/mutate.
 */

export type TabId =
  | "monetary"
  | "fiscal"
  | "demographics"
  | "trade"
  | "geopolitics";

// Backend vector group ("top" => country-level scalar, sent as group "").
export type VectorGroup =
  | "monetary"
  | "fiscal"
  | "labor"
  | "industrial"
  | "geopolitical"
  | "top";

export interface MacroFeature {
  key: string;
  label: string;
  tab: TabId;
  group: VectorGroup;
  field: string;
  min: number;
  max: number;
  step: number;
  unit: string; // "%", "idx", "x", "USD", "yrs", "ratio", "ppl"
  fraction?: boolean; // backend stores a fraction; UI shows a percentage
  control?: "range" | "number";
}

export const MACRO_TABS: { id: TabId; label: string }[] = [
  { id: "monetary", label: "Monetary" },
  { id: "fiscal", label: "Fiscal" },
  { id: "demographics", label: "Demographics" },
  { id: "trade", label: "Trade & Resources" },
  { id: "geopolitics", label: "Geopolitics" },
];

// Vibrant, light-theme continent palette (toy-block look). Cap = bright fill,
// side = a darker shade of the same hue to emphasise the 3D extrusion.
export const CONTINENT_CAP: Record<string, string> = {
  Asia: "#fbbf24",
  Europe: "#a78bfa",
  "North America": "#34d399",
  "South America": "#10b981",
  Africa: "#fb923c",
  Oceania: "#f472b6",
  Antarctica: "#cbd5e1",
  "Seven seas (open ocean)": "#7dd3fc",
};
export const CONTINENT_SIDE: Record<string, string> = {
  Asia: "#d97706",
  Europe: "#7c3aed",
  "North America": "#059669",
  "South America": "#047857",
  Africa: "#ea580c",
  Oceania: "#db2777",
  Antarctica: "#94a3b8",
  "Seven seas (open ocean)": "#0ea5e9",
};
export const DEFAULT_CAP = "#93c5fd";
export const DEFAULT_SIDE = "#3b82f6";

export function continentCap(continent: string): string {
  return CONTINENT_CAP[continent] ?? DEFAULT_CAP;
}
export function continentSide(continent: string): string {
  return CONTINENT_SIDE[continent] ?? DEFAULT_SIDE;
}

// Comprehensive ISO Alpha-3 -> full official English name. Used as a fallback;
// the component prefers the GeoJSON feature's own ADMIN/NAME, then this map,
// then the raw code (which is never shown to the user in practice).
export const ISO3_TO_NAME: Record<string, string> = {
  AFG: "Afghanistan", ALB: "Albania", DZA: "Algeria", AGO: "Angola", ARG: "Argentina",
  ARM: "Armenia", AUS: "Australia", AUT: "Austria", AZE: "Azerbaijan", BHS: "Bahamas",
  BHR: "Bahrain", BGD: "Bangladesh", BLR: "Belarus", BEL: "Belgium", BLZ: "Belize",
  BEN: "Benin", BTN: "Bhutan", BOL: "Bolivia", BIH: "Bosnia and Herzegovina",
  BWA: "Botswana", BRA: "Brazil", BRN: "Brunei", BGR: "Bulgaria", BFA: "Burkina Faso",
  BDI: "Burundi", KHM: "Cambodia", CMR: "Cameroon", CAN: "Canada",
  CAF: "Central African Republic", TCD: "Chad", CHL: "Chile", CHN: "China",
  COL: "Colombia", COG: "Republic of the Congo", COD: "Democratic Republic of the Congo",
  CRI: "Costa Rica", CIV: "Ivory Coast", HRV: "Croatia", CUB: "Cuba", CYP: "Cyprus",
  CZE: "Czechia", DNK: "Denmark", DJI: "Djibouti", DOM: "Dominican Republic",
  ECU: "Ecuador", EGY: "Egypt", SLV: "El Salvador", GNQ: "Equatorial Guinea",
  ERI: "Eritrea", EST: "Estonia", SWZ: "Eswatini", ETH: "Ethiopia", FJI: "Fiji",
  FIN: "Finland", FRA: "France", GAB: "Gabon", GMB: "Gambia", GEO: "Georgia",
  DEU: "Germany", GHA: "Ghana", GRC: "Greece", GRL: "Greenland", GTM: "Guatemala",
  GIN: "Guinea", GNB: "Guinea-Bissau", GUY: "Guyana", HTI: "Haiti", HND: "Honduras",
  HUN: "Hungary", ISL: "Iceland", IND: "India", IDN: "Indonesia", IRN: "Iran",
  IRQ: "Iraq", IRL: "Ireland", ISR: "Israel", ITA: "Italy", JAM: "Jamaica",
  JPN: "Japan", JOR: "Jordan", KAZ: "Kazakhstan", KEN: "Kenya", KWT: "Kuwait",
  KGZ: "Kyrgyzstan", LAO: "Laos", LVA: "Latvia", LBN: "Lebanon", LSO: "Lesotho",
  LBR: "Liberia", LBY: "Libya", LTU: "Lithuania", LUX: "Luxembourg",
  MDG: "Madagascar", MWI: "Malawi", MYS: "Malaysia", MLI: "Mali", MRT: "Mauritania",
  MEX: "Mexico", MDA: "Moldova", MNG: "Mongolia", MNE: "Montenegro", MAR: "Morocco",
  MOZ: "Mozambique", MMR: "Myanmar", NAM: "Namibia", NPL: "Nepal", NLD: "Netherlands",
  NZL: "New Zealand", NIC: "Nicaragua", NER: "Niger", NGA: "Nigeria",
  PRK: "North Korea", MKD: "North Macedonia", NOR: "Norway", OMN: "Oman",
  PAK: "Pakistan", PAN: "Panama", PNG: "Papua New Guinea", PRY: "Paraguay",
  PER: "Peru", PHL: "Philippines", POL: "Poland", PRT: "Portugal", QAT: "Qatar",
  ROU: "Romania", RUS: "Russia", RWA: "Rwanda", SAU: "Saudi Arabia", SEN: "Senegal",
  SRB: "Serbia", SLE: "Sierra Leone", SGP: "Singapore", SVK: "Slovakia",
  SVN: "Slovenia", SOM: "Somalia", ZAF: "South Africa", KOR: "South Korea",
  SSD: "South Sudan", ESP: "Spain", LKA: "Sri Lanka", SDN: "Sudan", SUR: "Suriname",
  SWE: "Sweden", CHE: "Switzerland", SYR: "Syria", TWN: "Taiwan", TJK: "Tajikistan",
  TZA: "Tanzania", THA: "Thailand", TLS: "Timor-Leste", TGO: "Togo",
  TTO: "Trinidad and Tobago", TUN: "Tunisia", TUR: "Turkey", TKM: "Turkmenistan",
  UGA: "Uganda", UKR: "Ukraine", ARE: "United Arab Emirates", GBR: "United Kingdom",
  USA: "United States", URY: "Uruguay", UZB: "Uzbekistan", VEN: "Venezuela",
  VNM: "Vietnam", YEM: "Yemen", ZMB: "Zambia", ZWE: "Zimbabwe", PSE: "Palestine",
  ATA: "Antarctica", XKX: "Kosovo",
};

export function countryName(code: string, geoName?: string): string {
  if (geoName && geoName !== "-99") return geoName;
  return ISO3_TO_NAME[code] ?? code;
}

// Compact factory keeps the 110+ feature definitions readable.
function mk(
  group: VectorGroup,
  tab: TabId,
  field: string,
  label: string,
  min: number,
  max: number,
  step: number,
  unit: string,
  fraction = false,
  control: "range" | "number" = "range",
): MacroFeature {
  return { key: `${group}.${field}`, label, tab, group, field, min, max, step, unit, fraction, control };
}

const MONETARY_FEATURES: MacroFeature[] = [
  mk("monetary", "monetary", "interest_rate", "Policy Interest Rate", 0, 25, 0.1, "%", true),
  mk("monetary", "monetary", "inflation_target", "Inflation Target", 0, 10, 0.1, "%", true),
  mk("monetary", "monetary", "inflation_cpi", "Inflation (CPI)", -5, 50, 0.1, "%", true),
  mk("monetary", "monetary", "inflation_ppi", "Inflation (PPI)", -5, 50, 0.1, "%", true),
  mk("monetary", "monetary", "core_inflation", "Core Inflation", -5, 40, 0.1, "%", true),
  mk("monetary", "monetary", "money_supply_m1", "M1 Money Supply", 0, 5e13, 1e10, "USD", false, "number"),
  mk("monetary", "monetary", "money_supply_m2", "M2 Money Supply", 0, 8e13, 1e10, "USD", false, "number"),
  mk("monetary", "monetary", "money_supply_m3", "M3 Money Supply", 0, 1e14, 1e10, "USD", false, "number"),
  mk("monetary", "monetary", "m2_growth", "M2 Growth", -10, 40, 0.1, "%", true),
  mk("monetary", "monetary", "velocity_of_money", "Velocity of Money", 0, 5, 0.05, "x"),
  mk("monetary", "monetary", "central_bank_reserves", "Central Bank Reserves", 0, 5e12, 1e9, "USD", false, "number"),
  mk("monetary", "monetary", "reserve_requirement", "Reserve Requirement Ratio", 0, 30, 0.5, "%", true),
  mk("monetary", "monetary", "discount_rate", "Discount Rate", 0, 25, 0.1, "%", true),
  mk("monetary", "monetary", "qe_intensity", "Central Bank Asset Purchases (QE)", 0, 100, 1, "%", true),
  mk("monetary", "monetary", "credit_growth", "Credit Growth", -10, 40, 0.1, "%", true),
  mk("monetary", "monetary", "real_interest_rate", "Real Interest Rate", -10, 20, 0.1, "%", true),
  mk("monetary", "monetary", "yield_2y", "2Y Treasury Yield", 0, 25, 0.1, "%", true),
  mk("monetary", "monetary", "yield_10y", "10Y Treasury Yield", 0, 25, 0.1, "%", true),
  mk("monetary", "monetary", "fx_spot", "FX Spot Rate (per USD)", 0.01, 200000, 0.01, "FX", false, "number"),
  mk("monetary", "monetary", "fx_forward_12m", "FX Forward 12M (per USD)", 0.01, 200000, 0.01, "FX", false, "number"),
];
const FISCAL_FEATURES: MacroFeature[] = [
  mk("top", "fiscal", "gdp_growth", "Real GDP Growth", -20, 20, 0.1, "%", true),
  mk("fiscal", "fiscal", "corporate_tax", "Corporate Tax", 0, 60, 0.5, "%", true),
  mk("fiscal", "fiscal", "individual_tax", "Individual Income Tax", 0, 75, 0.5, "%", true),
  mk("fiscal", "fiscal", "vat", "VAT / Sales Tax", 0, 40, 0.5, "%", true),
  mk("fiscal", "fiscal", "capital_gains_tax", "Capital Gains Tax", 0, 60, 0.5, "%", true),
  mk("fiscal", "fiscal", "property_tax", "Property Tax", 0, 10, 0.1, "%", true),
  mk("geopolitical", "fiscal", "defense_spending_pct", "Defense Budget (% GDP)", 0, 20, 0.1, "%", true),
  mk("fiscal", "fiscal", "healthcare_budget_pct", "Healthcare Budget (% GDP)", 0, 25, 0.1, "%", true),
  mk("fiscal", "fiscal", "education_budget_pct", "Education Budget (% GDP)", 0, 20, 0.1, "%", true),
  mk("fiscal", "fiscal", "public_investment_pct", "Infrastructure Spend (% GDP)", 0, 15, 0.1, "%", true),
  mk("fiscal", "fiscal", "govt_spending_pct", "Government Spending (% GDP)", 0, 80, 0.5, "%", true),
  mk("fiscal", "fiscal", "govt_debt_to_gdp", "Gov Debt-to-GDP", 0, 400, 1, "%", true),
  mk("fiscal", "fiscal", "budget_deficit_pct", "Budget Deficit (% GDP)", -20, 20, 0.1, "%", true),
  mk("fiscal", "fiscal", "tax_revenue_pct", "Tax Revenue (% GDP)", 0, 60, 0.5, "%", true),
  mk("fiscal", "fiscal", "customs_revenue_pct", "Customs Revenue (% GDP)", 0, 10, 0.1, "%", true),
  mk("fiscal", "fiscal", "trade_balance_pct", "Trade Balance (% GDP)", -30, 30, 0.1, "%", true),
  mk("fiscal", "fiscal", "current_account_pct", "Current Account (% GDP)", -30, 30, 0.1, "%", true),
  mk("fiscal", "fiscal", "avg_import_tariff", "Import Tariff Average", 0, 50, 0.5, "%", true),
  mk("fiscal", "fiscal", "avg_export_subsidy", "Export Subsidy Rate", 0, 30, 0.5, "%", true),
  mk("fiscal", "fiscal", "informal_economy_pct", "Informal Economy (% GDP)", 0, 60, 0.5, "%", true),
  mk("fiscal", "fiscal", "export_index", "Export Index", 10, 400, 1, "idx"),
  mk("fiscal", "fiscal", "import_index", "Import Index", 10, 400, 1, "idx"),
  mk("fiscal", "fiscal", "fdi_inflow", "FDI Inflows", 0, 2e12, 1e9, "USD", false, "number"),
  mk("fiscal", "fiscal", "fdi_outflow", "FDI Outflows", 0, 2e12, 1e9, "USD", false, "number"),
  mk("fiscal", "fiscal", "foreign_reserves", "Foreign Reserves", 0, 5e12, 1e9, "USD", false, "number"),
  mk("fiscal", "fiscal", "sovereign_rating", "Sovereign Rating", 0, 1, 0.01, "ratio"),
  mk("fiscal", "fiscal", "ease_of_business", "Ease of Doing Business", 0, 1, 0.01, "ratio"),
];
const DEMOGRAPHIC_FEATURES: MacroFeature[] = [
  mk("labor", "demographics", "population", "Total Population", 0, 2e9, 1e6, "ppl", false, "number"),
  mk("labor", "demographics", "workforce_participation", "Workforce Participation Rate", 0, 100, 0.5, "%", true),
  mk("labor", "demographics", "unemployment", "Unemployment Rate", 0, 45, 0.1, "%", true),
  mk("labor", "demographics", "youth_unemployment", "Youth Unemployment", 0, 70, 0.1, "%", true),
  mk("labor", "demographics", "underemployment", "Underemployment Rate", 0, 45, 0.1, "%", true),
  mk("labor", "demographics", "wage_growth", "Wage Growth", -10, 30, 0.1, "%", true),
  mk("labor", "demographics", "minimum_wage", "Minimum Wage (/hr)", 0, 100, 0.25, "cur", false, "number"),
  mk("labor", "demographics", "retirement_age", "Retirement Age", 50, 75, 0.5, "yrs"),
  mk("labor", "demographics", "immigration_quota", "Immigration Quota", 0, 5, 0.05, "%", true),
  mk("labor", "demographics", "productivity_index", "Productivity Index", 50, 200, 1, "idx"),
  mk("labor", "demographics", "dependency_ratio", "Dependency Ratio", 0, 1.5, 0.01, "ratio"),
  mk("labor", "demographics", "birth_rate", "Birth Rate", 0, 5, 0.05, "%", true),
  mk("labor", "demographics", "death_rate", "Death Rate", 0, 5, 0.05, "%", true),
  mk("labor", "demographics", "net_migration", "Net Migration Rate", -3, 3, 0.05, "%", true),
  mk("labor", "demographics", "median_age", "Median Age", 15, 60, 0.5, "yrs"),
  mk("labor", "demographics", "urbanization", "Urbanization Rate", 0, 100, 0.5, "%", true),
  mk("labor", "demographics", "gini_coefficient", "Gini Coefficient", 0, 1, 0.01, "ratio"),
  mk("labor", "demographics", "literacy_rate", "Literacy Rate", 0, 100, 0.5, "%", true),
  mk("labor", "demographics", "tertiary_education", "Tertiary Education Rate", 0, 100, 0.5, "%", true),
  mk("labor", "demographics", "healthcare_index", "Healthcare Index", 0, 1, 0.01, "ratio"),
  mk("labor", "demographics", "labor_cost_index", "Labor Cost Index", 20, 300, 1, "idx"),
  mk("labor", "demographics", "union_density", "Union Density", 0, 100, 0.5, "%", true),
];
const TRADE_FEATURES: MacroFeature[] = [
  mk("industrial", "trade", "manufacturing_pmi", "Manufacturing PMI", 30, 70, 0.5, "idx"),
  mk("industrial", "trade", "services_pmi", "Services PMI", 30, 70, 0.5, "idx"),
  mk("industrial", "trade", "industrial_production", "Industrial Production", 50, 200, 1, "idx"),
  mk("industrial", "trade", "capacity_utilization", "Capacity Utilization", 40, 100, 0.5, "%", true),
  mk("industrial", "trade", "energy_production", "Energy Production", 0, 300, 1, "idx"),
  mk("industrial", "trade", "energy_consumption", "Energy Consumption", 0, 300, 1, "idx"),
  mk("industrial", "trade", "energy_independence", "Energy Independence", 0, 2, 0.01, "ratio"),
  mk("industrial", "trade", "oil_reserves", "Strategic Oil Reserves", 0, 5e11, 1e8, "bbl", false, "number"),
  mk("industrial", "trade", "gas_reserves", "Natural Gas Reserves", 0, 5e13, 1e10, "m3", false, "number"),
  mk("industrial", "trade", "coal_reserves", "Coal Reserves", 0, 5e11, 1e8, "t", false, "number"),
  mk("industrial", "trade", "rare_earth_reserves", "Rare Earth Reserves", 0, 1e8, 1e5, "t", false, "number"),
  mk("industrial", "trade", "semiconductor_capacity", "Semiconductor Capacity (share)", 0, 50, 0.5, "%", true),
  mk("industrial", "trade", "agricultural_output", "Agricultural Output", 0, 300, 1, "idx"),
  mk("industrial", "trade", "food_security_index", "Food Security Index", 0, 1, 0.01, "ratio"),
  mk("industrial", "trade", "water_stress_index", "Water Stress Index", 0, 1, 0.01, "ratio"),
  mk("industrial", "trade", "supply_chain_friction", "Supply Chain Friction", 0, 1, 0.01, "ratio"),
  mk("industrial", "trade", "logistics_performance", "Logistics Performance", 0, 1, 0.01, "ratio"),
  mk("industrial", "trade", "rnd_spending_pct", "Corporate R&D Spend (% GDP)", 0, 10, 0.1, "%", true),
  mk("industrial", "trade", "corporate_rnd_subsidy", "Corporate R&D Subsidy (% GDP)", 0, 10, 0.1, "%", true),
  mk("industrial", "trade", "patents_index", "Patents Index", 0, 300, 1, "idx"),
  mk("industrial", "trade", "infrastructure_index", "Infrastructure Index", 0, 1, 0.01, "ratio"),
  mk("industrial", "trade", "carbon_intensity", "Carbon Intensity", 0, 1, 0.01, "ratio"),
  mk("industrial", "trade", "renewable_share", "Renewable Energy Share", 0, 100, 0.5, "%", true),
  mk("industrial", "trade", "clean_energy_subsidy", "Clean Energy Subsidy (% GDP)", 0, 10, 0.1, "%", true),
  mk("industrial", "trade", "tech_export_controls", "Tech Export Controls", 0, 1, 0.01, "ratio"),
];
const GEOPOLITICS_FEATURES: MacroFeature[] = [
  mk("geopolitical", "geopolitics", "military_strength_index", "Military Strength", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "nuclear_capability", "Nuclear Capability", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "cyber_capability", "Cyber Capability", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "political_stability", "Political Stability", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "corruption_index", "Anti-Corruption Index", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "press_freedom", "Press Freedom", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "consumer_confidence", "Consumer Confidence", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "business_confidence", "Business Confidence", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "social_unrest_index", "Social Unrest Index", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "soft_power_index", "Soft Power Index", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "diplomatic_reach", "Diplomatic Reach", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "sanctions_exposure", "Sanctions Exposure", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "energy_security", "Energy Security", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "alliance_cohesion", "Alliance Cohesion", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "public_approval", "Public Approval", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "election_risk", "Election Risk", 0, 1, 0.01, "ratio"),
  mk("geopolitical", "geopolitics", "geopolitical_risk", "Geopolitical Risk", 0, 1, 0.01, "ratio"),
];

export const MACRO_FEATURES: MacroFeature[] = [
  ...MONETARY_FEATURES,
  ...FISCAL_FEATURES,
  ...DEMOGRAPHIC_FEATURES,
  ...TRADE_FEATURES,
  ...GEOPOLITICS_FEATURES,
];

export function featuresForTab(tab: TabId): MacroFeature[] {
  return MACRO_FEATURES.filter((f) => f.tab === tab);
}
