/**
 * ECONITH World :: Hub & Proxy simulation model.
 *
 * Tiered `CountryNode` model backing the 50-node economy:
 *
 *   HubNode   — Tier 1. Runs the full independent macro loop and BROADCASTS
 *               "Global Macro Shocks" to its dependent proxies on user edits.
 *   ProxyNode — Tier 2. Runs a derivative/correlation engine: its correlated
 *               features drift toward a weighted blend of its hubs, UNLESS the
 *               user manually overrides a feature (which locks it + fires a
 *               localized shock event).
 *
 * Everything here is framework-agnostic (no React). The state slice in
 * `contexts/WorldSimContext.tsx` owns orchestration, ticking and the log queue.
 */
import type { CountryMacro, CountryVectors } from "@/hooks/useMetricsStream";
import { COUNTRY_REFERENCE } from "@/constants/countryReference";
import { ISO3_TO_NAME } from "@/constants/macroFeatures";
import { isSimNation, isReferenceOnly } from "@/constants/simNations";
import {
  ALL_NATION_CODES,
  CODE_CONTINENT,
  CORRELATED_FIELDS,
  DIVERSION_BENEFICIARIES,
  HUB_PROFILES,
  type HubCode,
  type HubWeight,
  type NodeTier,
  defaultVectors,
  dependenciesFor,
  isHub,
} from "@/constants/worldGraph";
import type { SimMessageKey } from "@/lib/i18n/types";

// A macro field address: which vector group + field name.
export type FieldGroup = keyof CountryVectors | "top";
export interface FieldAddr {
  group: FieldGroup;
  field: string;
}

export interface SimEvent {
  id: string;
  ts: number;                 // epoch ms (assigned by the state slice)
  level: "info" | "ok" | "warn" | "danger";
  source: string;             // policy | trade | contagion | corporate | society
  country: string;            // display name (ISO code preferred for i18n)
  messageKey: SimMessageKey;
  messageParams: Record<string, string | number>;
}

function clone(v: CountryVectors): CountryVectors {
  return {
    monetary: { ...v.monetary },
    fiscal: { ...v.fiscal },
    labor: { ...v.labor },
    industrial: { ...v.industrial },
    geopolitical: { ...v.geopolitical },
  };
}

function applyProfileGroup(target: Record<string, number>, patch?: Partial<Record<string, number>>) {
  if (!patch) return;
  for (const [k, val] of Object.entries(patch)) {
    if (typeof val === "number") target[k] = val;
  }
}

// ---------------------------------------------------------------------------
// Base node — shared feature access + CountryMacro projection.
// ---------------------------------------------------------------------------
export abstract class CountryNode {
  readonly code: string;
  readonly name: string;
  readonly continent: string;
  abstract readonly tier: NodeTier;

  gdp: number;
  gdp_growth: number;
  gdp_per_capita: number;
  vectors: CountryVectors;

  /** Feature keys (`group.field`) the user has manually pinned. */
  readonly overrides = new Set<string>();

  constructor(code: string, name: string, continent: string, gdp: number, gdpGrowth: number) {
    this.code = code;
    this.name = name;
    this.continent = continent;
    this.gdp = gdp;
    this.gdp_growth = gdpGrowth;
    this.vectors = defaultVectors();
    const pop = COUNTRY_REFERENCE[code]?.population ?? 30_000_000;
    this.vectors.labor.population = pop;
    this.gdp_per_capita = pop > 0 ? gdp / pop : 0;
  }

  getFeature({ group, field }: FieldAddr): number {
    if (group === "top") {
      if (field === "gdp_growth") return this.gdp_growth;
      if (field === "gdp") return this.gdp;
      return 0;
    }
    return this.vectors[group]?.[field] ?? 0;
  }

  setFeature({ group, field }: FieldAddr, value: number): void {
    if (group === "top") {
      if (field === "gdp_growth") this.gdp_growth = value;
      else if (field === "gdp") this.gdp = value;
      return;
    }
    if (this.vectors[group] && field in this.vectors[group]) {
      this.vectors[group][field] = value;
    }
  }

  /** Project into the wire-format `CountryMacro` the existing UI renders from. */
  toMacro(): CountryMacro {
    return {
      code: this.code,
      name: this.name,
      continent: this.continent,
      gdp: this.gdp,
      gdp_per_capita: this.gdp_per_capita,
      gdp_growth: this.gdp_growth,
      inflation: this.vectors.monetary.inflation_cpi,
      interest_rate: this.vectors.monetary.interest_rate,
      tax: this.vectors.fiscal.corporate_tax,
      population: this.vectors.labor.population,
      unemployment: this.vectors.labor.unemployment,
      vectors: clone(this.vectors),
    };
  }
}

// ---------------------------------------------------------------------------
// Tier 1 — Core Hub.
// ---------------------------------------------------------------------------
export class HubNode extends CountryNode {
  readonly tier = "hub" as const;

  constructor(code: HubCode) {
    const profile = HUB_PROFILES[code];
    super(code, profile.name, CODE_CONTINENT[code] ?? "Asia", profile.gdp, profile.gdp_growth);
    applyProfileGroup(this.vectors.monetary, profile.monetary);
    applyProfileGroup(this.vectors.fiscal, profile.fiscal);
    applyProfileGroup(this.vectors.labor, profile.labor);
    applyProfileGroup(this.vectors.industrial, profile.industrial);
    applyProfileGroup(this.vectors.geopolitical, profile.geopolitical);
    this.gdp_per_capita =
      this.vectors.labor.population > 0 ? this.gdp / this.vectors.labor.population : 0;
  }

  /**
   * Independent macro loop for a hub. Gentle mean-reversion toward a policy-
   * consistent equilibrium (Taylor-ish growth, Okun unemployment, FX carry),
   * modulated by confidence & unrest. `dt` is a per-tick scale (1 == one day).
   */
  step(dt: number): void {
    const m = this.vectors.monetary;
    const f = this.vectors.fiscal;
    const lab = this.vectors.labor;
    const g = this.vectors.geopolitical;

    const realRate = m.interest_rate - m.inflation_cpi;
    let growthTarget =
      0.02 +
      0.04 * (g.business_confidence - 0.5) +
      0.6 * f.trade_balance_pct -
      0.35 * realRate -
      0.02 * f.avg_import_tariff -
      0.05 * g.social_unrest_index;
    growthTarget = Math.max(-0.15, Math.min(0.15, growthTarget));
    if (!this.overrides.has("top.gdp_growth")) {
      this.gdp_growth += (growthTarget - this.gdp_growth) * 0.08 * dt;
    }
    this.gdp *= 1 + (this.gdp_growth * dt) / 365;
    if (lab.population > 0) this.gdp_per_capita = this.gdp / lab.population;

    if (!this.overrides.has("labor.unemployment")) {
      const uTarget = Math.max(0.01, 0.05 - 0.8 * (this.gdp_growth - 0.02));
      lab.unemployment += (uTarget - lab.unemployment) * 0.05 * dt;
    }
    // Inflation cools with a positive real rate (only if not pinned).
    if (!this.overrides.has("monetary.inflation_cpi")) {
      const drift = -0.1 * realRate - 0.05 * (m.inflation_cpi - m.inflation_target);
      m.inflation_cpi = clampFrac(m.inflation_cpi + Math.max(-0.004, Math.min(0.004, drift)) * dt);
    }
  }
}

// ---------------------------------------------------------------------------
// Tier 2 — Proxy Node.
// ---------------------------------------------------------------------------
export class ProxyNode extends CountryNode {
  readonly tier = "proxy" as const;
  readonly hubs: HubWeight[];

  constructor(code: string) {
    const ref = COUNTRY_REFERENCE[code];
    const name = ISO3_TO_NAME[code] ?? code;
    const continent = CODE_CONTINENT[code] ?? "Europe";
    // Seed GDP from population * a plausible per-capita band so proxies aren't clones.
    const pop = ref?.population ?? 30_000_000;
    const seedGdp = pop * 18_000;
    super(code, name, continent, seedGdp, 0.025);
    this.hubs = dependenciesFor(code);
  }

  /** Weighted blend of this proxy's hubs for one correlated field. */
  private hubBlend(addr: FieldAddr, hubMap: Map<string, CountryNode>): number | null {
    let acc = 0;
    let w = 0;
    for (const dep of this.hubs) {
      const hub = hubMap.get(dep.hub);
      if (!hub) continue;
      acc += hub.getFeature(addr) * dep.weight;
      w += dep.weight;
    }
    return w > 0 ? acc / w : null;
  }

  /**
   * Derivative engine: each correlated feature drifts toward its hub blend.
   * Manually-overridden features are LOCKED (user precedence). `pull` is the
   * per-tick correlation speed.
   */
  step(dt: number, hubMap: Map<string, CountryNode>, pull = 0.06): void {
    for (const addr of CORRELATED_FIELDS) {
      const key = `${addr.group}.${addr.field}`;
      if (this.overrides.has(key)) continue;
      const target = this.hubBlend(addr, hubMap);
      if (target === null) continue;
      const cur = this.getFeature(addr);
      this.setFeature(addr, cur + (target - cur) * pull * dt);
    }
    // Derived GDP path from its (possibly drifting) growth.
    this.gdp *= 1 + (this.gdp_growth * dt) / 365;
    if (this.vectors.labor.population > 0) {
      this.gdp_per_capita = this.gdp / this.vectors.labor.population;
    }
  }
}

export type AnyNode = HubNode | ProxyNode;

function clampFrac(x: number): number {
  return Math.max(-0.05, Math.min(0.6, x));
}

// ===========================================================================
//  Engine — construction, edits, cross-node cascades, tariff diversion.
// ===========================================================================
export type NodeMap = Map<string, AnyNode>;

/** Build all 50 seeded nodes (10 hubs + 40 proxies). */
export function buildWorld(): NodeMap {
  const map: NodeMap = new Map();
  for (const code of ALL_NATION_CODES) {
    map.set(code, isHub(code) ? new HubNode(code as HubCode) : new ProxyNode(code));
  }
  return map;
}

/** Lazily ensure a node exists (declared proxies + stray globe clicks). */
export function ensureNode(map: NodeMap, code: string): AnyNode | null {
  const existing = map.get(code);
  if (existing) return existing;
  if (!code || !isSimNation(code) || isReferenceOnly(code)) return null;
  const node = isHub(code)
    ? new HubNode(code as HubCode)
    : new ProxyNode(code);
  map.set(code, node);
  return node;
}

const HUB_LIST: HubCode[] = Object.keys(HUB_PROFILES) as HubCode[];

/** Snapshot of just the hubs, for proxy correlation reads. */
function hubMapOf(map: NodeMap): Map<string, CountryNode> {
  const hubs = new Map<string, CountryNode>();
  for (const h of HUB_LIST) {
    const n = map.get(h);
    if (n) hubs.set(h, n);
  }
  return hubs;
}

/** Advance the whole world one tick. Returns any spontaneous events. */
export function stepWorld(map: NodeMap, dt: number): Omit<SimEvent, "id" | "ts">[] {
  const hubs = hubMapOf(map);
  const events: Omit<SimEvent, "id" | "ts">[] = [];

  for (const node of map.values()) {
    if (node.tier === "hub") node.step(dt);
  }
  for (const node of map.values()) {
    if (node.tier === "proxy") node.step(dt, hubs);
  }

  // Sparse spontaneous colour: only surface genuinely notable states so the
  // log stays readable (the queue throttles display regardless).
  for (const node of map.values()) {
    const unrest = node.vectors.geopolitical.social_unrest_index;
    if (unrest > 0.62 && Math.random() < 0.05) {
      events.push({
        level: "danger",
        source: "society",
        country: node.code,
        messageKey: "unrest",
        messageParams: {
          country: node.name,
          pct: (unrest * 100).toFixed(0),
        },
      });
    }
  }
  return events;
}

const PCT_FIELDS = new Set(["interest_rate", "inflation_cpi", "vat", "corporate_tax",
  "individual_tax", "gdp_growth", "unemployment", "avg_import_tariff"]);

function fmtValue(addr: FieldAddr, value: number, fraction: boolean): string {
  if (fraction || PCT_FIELDS.has(addr.field)) return `${(value * 100).toFixed(1)}%`;
  if (Math.abs(value) >= 1e9) return `$${(value / 1e9).toFixed(1)}B`;
  return value.toFixed(2);
}

export interface EditResult {
  events: Omit<SimEvent, "id" | "ts">[];
}

/**
 * Apply a user edit to any node.
 *
 * Hub edit  -> sets the value + BROADCASTS a Global Macro Shock to dependent
 *              proxies (correlated fields nudge immediately) with cascade events.
 * Proxy edit -> sets the value as a manual OVERRIDE (locks it from drift) and
 *               fires a localized shock event.
 *
 * `labelKey` is the macro feature key for i18n; `fraction` indicates %-typed fields.
 */
export function applyEdit(
  map: NodeMap,
  code: string,
  addr: FieldAddr,
  value: number,
  labelKey: string,
  fraction: boolean,
): EditResult {
  const node = ensureNode(map, code);
  if (!node) return { events: [] };

  const key = `${addr.group}.${addr.field}`;
  const prev = node.getFeature(addr);
  node.setFeature(addr, value);

  const events: Omit<SimEvent, "id" | "ts">[] = [];

  if (node.tier === "proxy") {
    // User precedence: lock the field from correlation drift.
    node.overrides.add(key);
    events.push({
      level: "warn",
      source: "policy",
      country: node.code,
      messageKey: "proxyOverride",
      messageParams: {
        country: node.name,
        labelKey,
        value: fmtValue(addr, value, fraction),
      },
    });
    return { events };
  }

  // ---- Hub edit: broadcast a Global Macro Shock to dependent proxies. ----
  const delta = value - prev;
  events.push({
    level: "warn",
    source: "policy",
    country: node.code,
    messageKey: "hubAdjust",
    messageParams: {
      country: node.name,
      labelKey,
      value: fmtValue(addr, value, fraction),
    },
  });

  if (Math.abs(delta) < 1e-9 || !isCorrelated(addr)) return { events };

  // Every proxy that depends on this hub feels a fraction of the shock.
  const affected: { node: ProxyNode; move: number }[] = [];
  for (const other of map.values()) {
    if (other.tier !== "proxy") continue;
    if (other.overrides.has(key)) continue;
    const dep = other.hubs.find((h) => h.hub === code);
    if (!dep) continue;
    const move = delta * dep.weight * 0.5; // immediate partial pass-through
    if (Math.abs(move) < 1e-9) continue;
    other.setFeature(addr, other.getFeature(addr) + move);
    affected.push({ node: other, move });
  }

  // Narrate the largest 3 chain reactions.
  affected.sort((a, b) => Math.abs(b.move) - Math.abs(a.move));
  for (const { node: pnode, move } of affected.slice(0, 3)) {
    const dirKey = move > 0 ? "rose" : "eased";
    events.push({
      level: "info",
      source: "contagion",
      country: pnode.code,
      messageKey: "proxyContagion",
      messageParams: {
        proxy: pnode.name,
        labelKey,
        dirKey,
        value: fmtValue(addr, pnode.getFeature(addr), fraction),
        hub: node.name,
      },
    });
  }
  return { events };
}

function isCorrelated(addr: FieldAddr): boolean {
  return CORRELATED_FIELDS.some((c) => c.group === addr.group && c.field === addr.field);
}

/**
 * Cross-node tariff logic. A hub imposing a tariff on a target:
 *   - depresses the target's export_index & growth,
 *   - lifts the imposer's imported inflation,
 *   - and diverts supply chains to alt-manufacturing nations (VNM, MEX, ...),
 * generating human-readable chain-reaction events.
 */
export function applyTariff(
  map: NodeMap,
  src: string,
  dst: string,
  rate: number,
  prevRate: number,
): { events: Omit<SimEvent, "id" | "ts">[] } {
  const source = ensureNode(map, src);
  const target = ensureNode(map, dst);
  const events: Omit<SimEvent, "id" | "ts">[] = [];
  if (!source || !target) return { events };

  const delta = rate - prevRate;
  if (Math.abs(delta) < 1e-4) return { events };

  // Target: exports and growth take the hit.
  const tExport = target.vectors.fiscal.export_index;
  target.vectors.fiscal.export_index = Math.max(10, tExport - 40 * delta);
  target.gdp_growth = Math.max(-0.2, target.gdp_growth - 0.4 * delta);
  // Imposer: imported goods get pricier.
  source.vectors.monetary.inflation_cpi = clampFrac(
    source.vectors.monetary.inflation_cpi + 0.15 * delta,
  );

  events.push({
    level: delta > 0 ? "danger" : "ok",
    source: "trade",
    country: source.code,
    messageKey: "tariffChange",
    messageParams: {
      source: source.name,
      actionKey: delta > 0 ? "raised" : "cut",
      target: target.name,
      rate: (rate * 100).toFixed(0),
      exportActionKey: delta > 0 ? "fall" : "recover",
      exportIdx: Math.abs(40 * delta).toFixed(1),
      cpiSign: delta > 0 ? "+" : "-",
      cpiDelta: Math.abs(15 * delta).toFixed(2),
    },
  });

  // Supply-chain diversion: alt manufacturers (not src/dst) capture trade.
  if (delta > 0) {
    for (const code of DIVERSION_BENEFICIARIES) {
      if (code === src || code === dst) continue;
      const bene = map.get(code);
      if (!bene) continue;
      const gain = 6 * delta * (bene.tier === "proxy" ? 0.6 : 1);
      if (gain < 0.4) continue;
      const beforePmi = bene.vectors.industrial.manufacturing_pmi;
      bene.vectors.fiscal.export_index += gain;
      bene.vectors.industrial.manufacturing_pmi = Math.min(70, beforePmi + gain * 0.25);
      events.push({
        level: "ok",
        source: "contagion",
        country: bene.code,
        messageKey: "supplyDiversion",
        messageParams: {
          country: bene.name,
          pts: (gain * 0.25).toFixed(1),
          source: source.name,
          target: target.name,
        },
      });
    }
  }
  return { events };
}

/** Reset a proxy's manual overrides so it rejoins its hub correlation. */
export function releaseOverrides(map: NodeMap, code: string): void {
  map.get(code)?.overrides.clear();
}

/** Project the whole world into the wire-format map the UI renders. */
export function projectCountries(map: NodeMap): Record<string, CountryMacro> {
  const out: Record<string, CountryMacro> = {};
  for (const [code, node] of map) out[code] = node.toMacro();
  return out;
}
