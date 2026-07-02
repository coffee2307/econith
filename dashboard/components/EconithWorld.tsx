"use client";

/**
 * ECONITH World :: interactive macro-geopolitical simulator.
 *
 * Rigid viewport: toolbar / (sidebar · globe · event log) / downbar.
 * Theme-aware toy-block earth (untextured sphere with thick extruded countries).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faMagnifyingGlass,
  faPlay,
  faPause,
  faSliders,
  faTableCells,
  faXmark,
  faArrowRightArrowLeft,
  faLayerGroup,
  faChartLine,
} from "@fortawesome/free-solid-svg-icons";
import { useTheme } from "@/contexts/ThemeContext";
import { useLocale } from "@/contexts/LocaleContext";
import { useMetrics } from "@/components/MetricsProvider";
import { useWorldSim } from "@/contexts/WorldSimContext";
import { EventLogQueue } from "@/components/EventLogQueue";
import { pauseTime, resumeTime, setTimeSpeed } from "@/lib/api";
import type { CountryMacro } from "@/hooks/useMetricsStream";
import {
  MACRO_TABS,
  MACRO_FEATURES,
  featuresForTab,
  continentCap,
  continentSide,
  ISO3_TO_NAME,
  type MacroFeature,
  type TabId,
} from "@/constants/macroFeatures";
import {
  featCode,
  featContinent,
  featName,
  centroidOf,
  resolveCountryQuery,
  mockCountryStats,
  type GeoFeature,
  type QuickStats,
} from "@/lib/geo";
import { HUB_CODES, dependenciesFor } from "@/constants/worldGraph";
import { isSimNation } from "@/constants/simNations";

const GLOBE_THEME = {
  dark: { bg: "#0a0a0b", material: "#1e3a5f", stroke: "#64748b", labelFg: "#ededef", labelBg: "#16161a", labelBorder: "#26262b" },
  light: { bg: "#f8fafc", material: "#bae6fd", stroke: "#334155", labelFg: "#0f172a", labelBg: "#ffffff", labelBorder: "#e2e8f0" },
} as const;

const GEOJSON_URL =
  "https://cdn.jsdelivr.net/gh/nvkelso/natural-earth-vector@master/geojson/ne_110m_admin_0_countries.geojson";
const SPEEDS = [1, 2, 5, 10, 20] as const;
const SIDEBAR_STORAGE = "econith-world-sidebars";
const SIDEBAR_DEFAULT = 288;
const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 420;
const DOWNBAR_STORAGE = "econith-world-downbar";
const DOWNBAR_DEFAULT = 144;
const DOWNBAR_MIN = 80;
const DOWNBAR_MAX = 480;
const SIDEBAR_LEVERS = [
  "monetary.interest_rate",
  "monetary.reserve_requirement",
  "fiscal.corporate_tax",
  "fiscal.individual_tax",
  "fiscal.vat",
  "geopolitical.defense_spending_pct",
];

interface GlobeInstance {
  controls: () => { autoRotate: boolean; autoRotateSpeed: number };
  pointOfView: (
    pov: { lat?: number; lng?: number; altitude?: number },
    ms?: number,
  ) => void;
}

// ---- value helpers ---------------------------------------------------------
function readRaw(country: CountryMacro | undefined, f: MacroFeature): number {
  if (!country) return 0;
  if (f.group === "top") {
    const v = (country as unknown as Record<string, number>)[f.field];
    return typeof v === "number" ? v : 0;
  }
  const g = country.vectors?.[f.group as keyof typeof country.vectors];
  const v = g ? g[f.field] : undefined;
  return typeof v === "number" ? v : 0;
}

function uiValue(
  country: CountryMacro | undefined,
  f: MacroFeature,
  overrides: Record<string, number>,
): number {
  if (f.key in overrides) return overrides[f.key];
  const raw = readRaw(country, f);
  return f.fraction ? raw * 100 : raw;
}

function fmtVal(v: number, f: MacroFeature): string {
  if (f.fraction || f.unit === "%") return `${v.toFixed(2)}%`;
  if (f.unit === "ratio" || f.unit === "x") return v.toFixed(2);
  if (f.unit === "yrs") return `${v.toFixed(1)}y`;
  if (Math.abs(v) >= 1e12) return `${(v / 1e12).toFixed(2)}T`;
  if (Math.abs(v) >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (Math.abs(v) >= 1e6) return `${(v / 1e6).toFixed(2)}M`;
  if (Math.abs(v) >= 1000)
    return v.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return v.toFixed(2);
}

// ===========================================================================
export default function EconithWorld() {
  const { theme } = useTheme();
  const { t, featureLabel, countryName, continentName } = useLocale();
  const { snapshot } = useMetrics();
  const sim = useWorldSim();
  const globePalette = GLOBE_THEME[theme];

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [GlobeComp, setGlobeComp] = useState<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [material, setMaterial] = useState<any>(null);
  const [features, setFeatures] = useState<GeoFeature[]>([]);
  const [selected, setSelected] = useState("USA");
  const [hovered, setHovered] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [popup, setPopup] = useState<
    { code: string; stats: QuickStats; x: number; y: number } | null
  >(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [modalTab, setModalTab] = useState<TabId>("monetary");
  const [search, setSearch] = useState("");
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [leftW, setLeftW] = useState(SIDEBAR_DEFAULT);
  const [rightW, setRightW] = useState(SIDEBAR_DEFAULT);
  const [downH, setDownH] = useState(DOWNBAR_DEFAULT);
  const leftWRef = useRef(SIDEBAR_DEFAULT);
  const rightWRef = useRef(SIDEBAR_DEFAULT);
  const downHRef = useRef(DOWNBAR_DEFAULT);

  useEffect(() => {
    leftWRef.current = leftW;
  }, [leftW]);
  useEffect(() => {
    rightWRef.current = rightW;
  }, [rightW]);
  useEffect(() => {
    downHRef.current = downH;
  }, [downH]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(SIDEBAR_STORAGE);
      if (!raw) return;
      const { left, right } = JSON.parse(raw) as { left?: number; right?: number };
      if (typeof left === "number") setLeftW(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, left)));
      if (typeof right === "number") setRightW(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, right)));
    } catch {
      /* ignore */
    }
    try {
      const h = localStorage.getItem(DOWNBAR_STORAGE);
      if (h) {
        const parsed = parseInt(h, 10);
        if (!Number.isNaN(parsed)) {
          setDownH(Math.min(DOWNBAR_MAX, Math.max(DOWNBAR_MIN, parsed)));
        }
      }
    } catch {
      /* ignore */
    }
  }, []);

  const persistSidebars = useCallback((left: number, right: number) => {
    try {
      localStorage.setItem(SIDEBAR_STORAGE, JSON.stringify({ left, right }));
    } catch {
      /* ignore */
    }
  }, []);

  const persistDownbar = useCallback((h: number) => {
    try {
      localStorage.setItem(DOWNBAR_STORAGE, String(h));
    } catch {
      /* ignore */
    }
  }, []);

  const globeRef = useRef<GlobeInstance | null>(null);
  const globeBoxRef = useRef<HTMLDivElement | null>(null);
  const centroids = useRef<Record<string, { lat: number; lng: number }>>({});

  const time = snapshot?.time;
  const running = time?.running ?? false;
  const multiplier = time?.multiplier ?? 1;
  const simDay = time?.sim_day ?? 0;

  // 50-node client world (Hub & Proxy engine) — every nation is editable.
  const countries = sim.countries;
  const simCodes = Object.keys(countries);
  const activeCode = selected;
  const activeCountry = countries[activeCode];
  const isSimulated = !!activeCountry; // always true for the 50 (+ ensured clicks)
  const activeTier = sim.tierOf(activeCode);

  // ---- lazy library + theme-aware globe material ----
  useEffect(() => {
    let mounted = true;
    Promise.all([import("react-globe.gl"), import("three")])
      .then(([globeMod, THREE]) => {
        if (!mounted) return;
        setGlobeComp(() => globeMod.default);
        setMaterial(
          new THREE.MeshPhongMaterial({
            color: globePalette.material,
            shininess: 6,
          }),
        );
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, [globePalette.material]);

  // ---- fetch polygons, build centroids + name index ----
  useEffect(() => {
    let mounted = true;
    fetch(GEOJSON_URL)
      .then((r) => r.json())
      .then((geo) => {
        if (!mounted || !geo?.features) return;
        const feats = geo.features as GeoFeature[];
        setFeatures(feats);
        const cen: Record<string, { lat: number; lng: number }> = {};
        for (const f of feats) {
          const code = featCode(f);
          if (!code) continue;
          cen[code] = centroidOf(f);
        }
        centroids.current = cen;
      })
      .catch(() => {});
    return () => {
      mounted = false;
    };
  }, []);

  // ---- responsive globe sizing ----
  useEffect(() => {
    const el = globeBoxRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ w: Math.floor(r.width), h: Math.floor(r.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // ---- auto-rotate ----
  useEffect(() => {
    const g = globeRef.current;
    if (!g) return;
    const c = g.controls();
    c.autoRotate = true;
    c.autoRotateSpeed = running ? Math.min(4, 0.25 * multiplier) : 0.05;
  }, [running, multiplier, GlobeComp]);

  // ---- reset overrides when switching country ----
  useEffect(() => {
    setOverrides({});
  }, [activeCode]);

  const draftFeature = useCallback((f: MacroFeature, uiVal: number) => {
    setOverrides((o) => ({ ...o, [f.key]: uiVal }));
  }, []);

  const hasDrafts = Object.keys(overrides).length > 0;

  const applyDrafts = useCallback(() => {
    if (!hasDrafts) return;
    for (const [key, uiVal] of Object.entries(overrides)) {
      const f = MACRO_FEATURES.find((x) => x.key === key);
      if (f) sim.editFeature(activeCode, f, uiVal);
    }
    setOverrides({});
  }, [activeCode, hasDrafts, overrides, sim]);

  const discardDrafts = useCallback(() => {
    setOverrides({});
  }, []);

  const selectCountry = useCallback(
    (code: string) => {
      if (!isSimNation(code)) return;
      sim.ensure(code);
      setSelected(code);
    },
    [sim],
  );

  const flyTo = useCallback(
    (code: string) => {
      const c = centroids.current[code];
      if (c && globeRef.current) {
        globeRef.current.pointOfView({ lat: c.lat, lng: c.lng, altitude: 2 }, 1500);
      }
      selectCountry(code);
    },
    [selectCountry],
  );

  const searchCountry = useCallback(() => {
    const code = resolveCountryQuery(search);
    if (code && centroids.current[code]) flyTo(code);
  }, [search, flyTo]);

  // ---- globe polygon accessors ----
  const capColor = (d: object): string => {
    const f = d as GeoFeature;
    const code = featCode(f);
    if (code && code === hovered) return "#ffffff";
    if (code && code === activeCode) return "#10b981";
    return continentCap(featContinent(f));
  };
  const sideColor = (d: object): string => continentSide(featContinent(d as GeoFeature));
  const altitude = (d: object): number => {
    const code = featCode(d as GeoFeature);
    if (code === activeCode) return 0.035;
    if (code === hovered) return 0.028;
    return 0.02;
  };

  const showGlobe = GlobeComp && material && size.w > 0 && size.h > 0;

  return (
    <div
      className="grid h-full min-h-0 overflow-hidden bg-base text-ink"
      style={{ gridTemplateRows: `auto minmax(0, 1fr) ${downH}px` }}
    >
      {/* ================= TOOLBAR (shared header lives in Navbar) ================= */}
      <div className="flex h-11 flex-none items-center justify-between gap-4 border-b border-line bg-surface px-4">
        <div className="flex items-center gap-2 rounded-xl border border-line bg-elevated px-3 py-1.5">
          <FontAwesomeIcon icon={faMagnifyingGlass} className="h-3.5 w-3.5 text-faint" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && searchCountry()}
            placeholder={t("world.searchPlaceholder")}
            className="w-44 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none sm:w-56"
          />
          <button
            onClick={searchCountry}
            className="rounded-lg bg-world px-2 py-0.5 text-xs font-medium text-black hover:bg-emerald-400"
          >
            {t("common.go")}
          </button>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 rounded-xl border border-line bg-elevated px-2 py-1">
            <button
              onClick={() => void (running ? pauseTime() : resumeTime())}
              aria-label={running ? t("common.pause") : t("common.play")}
              className="flex h-6 w-6 items-center justify-center rounded-lg bg-surface text-ink hover:bg-elevated"
            >
              <FontAwesomeIcon icon={running ? faPause : faPlay} className="h-3 w-3" />
            </button>
            {SPEEDS.map((s) => (
              <button
                key={s}
                onClick={() => void setTimeSpeed(s)}
                className={[
                  "rounded-lg px-1.5 py-0.5 font-mono text-[11px]",
                  multiplier === s
                    ? "bg-world text-black"
                    : "text-muted hover:text-ink",
                ].join(" ")}
              >
                {s}x
              </button>
            ))}
          </div>
          <div className="hidden font-mono text-xs text-muted md:block">
            {t("common.day")}{" "}
            <span className="font-semibold text-ink">{simDay.toLocaleString()}</span>
          </div>
        </div>
      </div>

      {/* ================= MIDDLE ================= */}
      <div
        className="grid min-h-0 overflow-hidden"
        style={{
          gridTemplateColumns: `${leftW}px 5px minmax(0, 1fr) 5px ${rightW}px`,
        }}
      >
        {/* LEFT SIDEBAR */}
        <aside className="flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-line bg-surface">
          <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4">
          <div className="mb-3 flex items-center gap-2">
            <FontAwesomeIcon icon={faSliders} className="h-4 w-4 text-world" />
            <h2 className="text-sm font-bold">{t("world.adjustMetrics")}</h2>
          </div>

          {/* Core Hub quick tabs (all 50 are selectable via globe / search) */}
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-faint">
            {t("world.coreHubs")}
          </p>
          <div className="mb-3 flex flex-wrap gap-1">
            {HUB_CODES.map((c) => (
              <button
                key={c}
                onClick={() => selectCountry(c)}
                title={countryName(c, ISO3_TO_NAME[c])}
                className={[
                  "rounded-md px-2 py-1 text-[11px] font-medium",
                  activeCode === c
                    ? "bg-world text-black"
                    : "border border-line text-muted hover:text-ink",
                ].join(" ")}
              >
                {c}
              </button>
            ))}
          </div>

          <div className="mb-4 rounded-xl border border-line bg-elevated p-3">
            <div className="flex items-center justify-between gap-2">
              <p className="text-base font-bold text-ink">
                {countryName(activeCode, activeCountry?.name ?? ISO3_TO_NAME[activeCode] ?? activeCode)}
              </p>
              <span
                className={[
                  "shrink-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase",
                  activeTier === "hub"
                    ? "bg-world/20 text-world"
                    : "border border-line text-muted",
                ].join(" ")}
              >
                {activeTier === "hub" ? t("world.coreHub") : t("world.proxyNode")}
              </span>
            </div>
            {activeCountry ? (
              <p className="mt-0.5 text-xs text-muted">
                {continentName(activeCountry.continent ?? "")} · {t("common.gdp")} $
                {((activeCountry.gdp ?? 0) / 1e12).toFixed(2)}T · {t("common.growth")}{" "}
                {(activeCountry.gdp_growth * 100).toFixed(2)}%
              </p>
            ) : null}
            {activeTier === "proxy" ? (
              <p className="mt-1 text-[11px] text-faint">
                {t("world.tracks")}{" "}
                {dependenciesFor(activeCode)
                  .map((d) => `${d.hub} ${(d.weight * 100).toFixed(0)}%`)
                  .join(" · ")}
              </p>
            ) : null}
          </div>

          <div className="space-y-3.5">
            {SIDEBAR_LEVERS.map((key) => {
              const f = MACRO_FEATURES.find((x) => x.key === key);
              if (!f) return null;
              return (
                <FeatureControl
                  key={key}
                  feature={f}
                  value={uiValue(activeCountry, f, overrides)}
                  overridden={sim.isOverridden(activeCode, f.key)}
                  pending={f.key in overrides}
                  onChange={(v) => draftFeature(f, v)}
                />
              );
            })}
          </div>

          <TariffControl
            activeCode={activeCode}
            simCodes={simCodes}
            tariffs={sim.tariffs}
            onImpose={sim.imposeTariff}
          />

          {hasDrafts ? (
            <div className="mt-4 flex gap-2">
              <button
                onClick={applyDrafts}
                className="flex flex-1 items-center justify-center rounded-xl bg-world py-2 text-sm font-semibold text-black hover:bg-emerald-400"
              >
                {t("world.apply")}
              </button>
              <button
                onClick={discardDrafts}
                className="rounded-xl border border-line px-3 py-2 text-xs text-muted hover:bg-elevated"
              >
                {t("world.discardDrafts")}
              </button>
            </div>
          ) : null}

          <button
            onClick={() => setModalOpen(true)}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-world py-2 text-sm font-semibold text-black hover:bg-emerald-400"
          >
            <FontAwesomeIcon icon={faLayerGroup} className="h-3.5 w-3.5" />
            {t("world.showAllFeatures", { count: MACRO_FEATURES.length })}
          </button>
          <button
            onClick={() => {
              setOverrides({});
              sim.resetOverrides(activeCode);
            }}
            className="mt-2 w-full rounded-xl border border-line py-1.5 text-xs text-muted hover:bg-elevated"
          >
            {t("world.releaseOverrides")}
          </button>
          </div>
        </aside>

        <ResizeHandle
          ariaLabel="Resize left panel"
          onResize={(dx) => {
            setLeftW((w) => Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w + dx)));
          }}
          onResizeEnd={() =>
            persistSidebars(leftWRef.current, rightWRef.current)
          }
        />

        {/* CENTER GLOBE */}
        <section
          ref={globeBoxRef}
          className="relative min-h-0 min-w-0 overflow-hidden"
          style={{ backgroundColor: globePalette.bg }}
        >
          {showGlobe ? (
            <div className="absolute inset-0 overflow-hidden">
            <GlobeComp
              ref={globeRef}
              width={size.w}
              height={size.h}
              backgroundColor={globePalette.bg}
              globeMaterial={material}
              showAtmosphere={false}
              polygonsData={features}
              polygonAltitude={altitude}
              polygonCapColor={capColor}
              polygonSideColor={sideColor}
              polygonStrokeColor={() => globePalette.stroke}
              polygonsTransitionDuration={200}
              polygonLabel={(d: object) =>
                `<div style="font:600 12px system-ui;color:${globePalette.labelFg};background:${globePalette.labelBg};padding:3px 7px;border-radius:6px;border:1px solid ${globePalette.labelBorder}">${featName(
                  d as GeoFeature,
                )}</div>`
              }
              onPolygonHover={(d: object | null) =>
                setHovered(d ? featCode(d as GeoFeature) : null)
              }
              onPolygonClick={(d: object, ev: MouseEvent) => {
                const f = d as GeoFeature;
                const code = featCode(f);
                if (!code) return;
                if (isSimNation(code)) selectCountry(code);
                const rect = globeBoxRef.current?.getBoundingClientRect();
                const x = rect ? ev.clientX - rect.left : 0;
                const y = rect ? ev.clientY - rect.top : 0;
                const simMacro = isSimNation(code) ? countries?.[code] : undefined;
                setPopup({
                  code,
                  stats: mockCountryStats(f, code, simMacro),
                  x,
                  y,
                });
              }}
              onGlobeReady={() => {
                const g = globeRef.current;
                if (g) {
                  g.controls().autoRotate = true;
                  g.controls().autoRotateSpeed = 0.25;
                  g.pointOfView({ lat: 25, lng: 10, altitude: 2.4 });
                }
              }}
            />
            </div>
          ) : (
            <div className="flex h-full w-full items-center justify-center">
              <p className="font-mono text-xs text-faint">{t("common.loadingGlobe")}</p>
            </div>
          )}

          {/* quick-inspect popup */}
          {popup ? (
            <div
              className="pointer-events-auto absolute z-20 w-56 rounded-xl border border-line bg-surface p-3"
              style={{
                left: Math.min(popup.x, (size.w || 800) - 240),
                top: Math.min(popup.y, (size.h || 600) - 150),
              }}
            >
              <div className="mb-1 flex items-center justify-between">
                <p className="text-sm font-bold text-ink">{popup.stats.name}</p>
                <button
                  onClick={() => setPopup(null)}
                  className="text-faint hover:text-ink"
                >
                  <FontAwesomeIcon icon={faXmark} className="h-3.5 w-3.5" />
                </button>
              </div>
              <dl className="space-y-1 text-xs">
                <StatRow label={t("common.gdp")} value={`$${(popup.stats.gdp / 1e9).toFixed(1)}B`} />
                <StatRow
                  label={t("common.population")}
                  value={popup.stats.population.toLocaleString()}
                />
                <StatRow
                  label={t("common.area")}
                  value={`${Math.round(popup.stats.area).toLocaleString()} km²`}
                />
                <StatRow
                  label={t("common.tier")}
                  value={
                    isSimNation(popup.code)
                      ? sim.tierOf(popup.code) === "hub"
                        ? t("world.coreHub")
                        : t("world.proxyNode")
                      : t("world.referenceOnly")
                  }
                />
              </dl>
              {isSimNation(popup.code) ? (
              <button
                onClick={() => {
                  selectCountry(popup.code);
                  setModalOpen(true);
                  setPopup(null);
                }}
                className="mt-2 w-full rounded-lg bg-world py-1 text-xs font-medium text-black hover:bg-emerald-400"
              >
                {t("world.inspectFeatures")}
              </button>
              ) : null}
            </div>
          ) : null}

          <div className="pointer-events-none absolute bottom-3 left-3 font-mono text-[11px] text-faint">
            {t("world.globeHint")}
          </div>
        </section>

        <ResizeHandle
          ariaLabel="Resize right panel"
          onResize={(dx) => {
            setRightW((w) => Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w - dx)));
          }}
          onResizeEnd={() =>
            persistSidebars(leftWRef.current, rightWRef.current)
          }
        />

        {/* RIGHT EVENT LOG (throttled LogQueue) */}
        <EventLogQueue events={sim.events} pendingCount={sim.pendingCount} />
      </div>

      {/* ================= DOWNBAR ================= */}
      <footer className="flex min-h-0 flex-col overflow-hidden border-t border-line bg-surface">
        <VerticalResizeHandle
          ariaLabel="Resize metric details panel"
          onResize={(dy) => {
            setDownH((h) => Math.min(DOWNBAR_MAX, Math.max(DOWNBAR_MIN, h - dy)));
          }}
          onResizeEnd={() => persistDownbar(downHRef.current)}
        />
        <div className="flex flex-none items-center gap-2 border-b border-line px-4 py-2">
          <FontAwesomeIcon icon={faTableCells} className="h-4 w-4 text-world" />
          <h2 className="text-sm font-bold">{t("world.metricDetails")}</h2>
          <span className="ml-1 text-xs text-faint">
            {countryName(activeCode, ISO3_TO_NAME[activeCode] ?? activeCode)} ·{" "}
            {isSimulated
              ? t("world.liveFeatures", { count: MACRO_FEATURES.length })
              : t("world.noSimulation")}
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-4 pb-3">
        {isSimulated ? (
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 md:grid-cols-3 lg:grid-cols-5">
            {MACRO_TABS.map((tab) => (
              <div key={tab.id}>
                <p className="mb-1 flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wide text-world">
                  <FontAwesomeIcon icon={faChartLine} className="h-3 w-3" />
                  {t(`macro.tabs.${tab.id}`)}
                </p>
                <div className="space-y-0.5">
                  {featuresForTab(tab.id).map((f) => (
                    <div
                      key={f.key}
                      className="flex items-center justify-between gap-2 text-[11px]"
                    >
                      <span className="truncate text-muted">{featureLabel(f.key)}</span>
                      <span className="shrink-0 font-mono font-medium text-ink">
                        {fmtVal(uiValue(activeCountry, f, {}), f)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="font-mono text-xs text-faint">
            {t("world.selectNation")}
          </p>
        )}
        </div>
      </footer>

      {/* ================= MODAL (all 111 features) ================= */}
      {modalOpen && isSimulated ? (
        <FeatureModal
          country={activeCountry}
          code={activeCode}
          overrides={overrides}
          hasDrafts={hasDrafts}
          modalTab={modalTab}
          setModalTab={setModalTab}
          onDraft={draftFeature}
          onApply={() => {
            applyDrafts();
          }}
          onDiscard={discardDrafts}
          onClose={() => setModalOpen(false)}
        />
      ) : null}
    </div>
  );
}

// ===========================================================================
//  Sub-components
// ===========================================================================
function StatRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-faint">{label}</dt>
      <dd className="font-mono font-medium text-ink">{value}</dd>
    </div>
  );
}

function FeatureControl({
  feature,
  value,
  overridden = false,
  pending = false,
  onChange,
}: {
  feature: MacroFeature;
  value: number;
  overridden?: boolean;
  pending?: boolean;
  onChange: (v: number) => void;
}) {
  const { featureLabel, t } = useLocale();

  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 truncate text-xs font-medium text-muted">
          {pending ? (
            <span
              title={t("world.draftTitle")}
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500"
            />
          ) : overridden ? (
            <span
              title={t("world.overrideTitle")}
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-world"
            />
          ) : null}
          {featureLabel(feature.key)}
        </span>
        <span className="shrink-0 font-mono text-xs font-semibold text-ink">
          {fmtVal(value, feature)}
        </span>
      </div>
      {feature.control === "number" ? (
        <input
          type="number"
          min={feature.min}
          max={feature.max}
          step={feature.step}
          value={Number.isFinite(value) ? value : 0}
          onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
          className="w-full rounded-lg border border-line bg-surface px-2 py-1 font-mono text-xs text-ink focus:border-world focus:outline-none"
        />
      ) : (
        <input
          type="range"
          min={feature.min}
          max={feature.max}
          step={feature.step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-line accent-world"
        />
      )}
    </div>
  );
}

function TariffControl({
  activeCode,
  simCodes,
  tariffs,
  onImpose,
}: {
  activeCode: string;
  simCodes: string[];
  tariffs?: Record<string, Record<string, number>>;
  onImpose: (src: string, dst: string, rate: number) => void;
}) {
  const { t, countryName } = useLocale();
  const others = simCodes.filter((c) => c !== activeCode);
  const [target, setTarget] = useState(others[0] ?? "");
  const [draft, setDraft] = useState<number | null>(null);
  useEffect(() => {
    setDraft(null);
    if (!others.includes(target)) setTarget(others[0] ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCode]);

  const live = (tariffs?.[activeCode]?.[target] ?? 0) * 100;
  const value = draft ?? live;

  return (
    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-800/40 dark:bg-amber-950/30">
      <div className="mb-2 flex items-center gap-2">
        <FontAwesomeIcon icon={faArrowRightArrowLeft} className="h-3.5 w-3.5 shrink-0 text-amber-600" />
        <span className="text-[11px] font-semibold leading-snug text-amber-700 dark:text-amber-400">
          {t("world.imposeTariffOn")}
        </span>
      </div>
      <select
        value={target}
        onChange={(e) => {
          setTarget(e.target.value);
          setDraft(null);
        }}
        className="mb-2 w-full rounded-lg border border-amber-200 bg-surface px-2 py-1 text-xs text-ink focus:outline-none dark:border-amber-800/50"
      >
        {others.map((c) => (
          <option key={c} value={c}>
            {countryName(c, ISO3_TO_NAME[c] ?? c)}
          </option>
        ))}
      </select>
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[11px] text-amber-700">{t("world.tariffRate")}</span>
        <span className="font-mono text-xs font-semibold text-amber-800">
          {value.toFixed(1)}%
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={50}
        step={1}
        value={value}
        onChange={(e) => setDraft(parseFloat(e.target.value))}
        className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-amber-200 accent-amber-500"
      />
      <button
        onClick={() => {
          if (draft !== null && target) onImpose(activeCode, target, draft / 100);
          setDraft(null);
        }}
        disabled={draft === null || !target}
        className="mt-2 w-full rounded-md bg-amber-500 py-1.5 text-xs font-semibold text-white hover:bg-amber-600 disabled:opacity-40"
      >
        {t("world.apply")}
      </button>
    </div>
  );
}

function FeatureModal({
  country,
  code,
  overrides,
  hasDrafts,
  modalTab,
  setModalTab,
  onDraft,
  onApply,
  onDiscard,
  onClose,
}: {
  country: CountryMacro | undefined;
  code: string;
  overrides: Record<string, number>;
  hasDrafts: boolean;
  modalTab: TabId;
  setModalTab: (t: TabId) => void;
  onDraft: (f: MacroFeature, v: number) => void;
  onApply: () => void;
  onDiscard: () => void;
  onClose: () => void;
}) {
  const { t, countryName } = useLocale();
  const list = featuresForTab(modalTab);
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 p-6"
      onClick={onClose}
    >
      <div
        className="flex h-[80vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-line bg-surface"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <div>
            <h3 className="text-base font-bold text-ink">
              {countryName(code, ISO3_TO_NAME[code] ?? code)} — {t("world.fullMacroVector")}
            </h3>
            <p className="text-xs text-faint">
              {t("world.modalSubtitle", { count: MACRO_FEATURES.length })}
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-lg text-faint hover:bg-elevated hover:text-ink"
          >
            <FontAwesomeIcon icon={faXmark} className="h-4 w-4" />
          </button>
        </div>

        <div className="flex flex-none gap-1 border-b border-line px-5 py-2">
          {MACRO_TABS.map((tabItem) => (
            <button
              key={tabItem.id}
              onClick={() => setModalTab(tabItem.id)}
              className={[
                "rounded-lg px-3 py-1.5 text-xs font-medium",
                modalTab === tabItem.id
                  ? "bg-world text-black"
                  : "text-muted hover:bg-elevated hover:text-ink",
              ].join(" ")}
            >
              {t(`macro.tabs.${tabItem.id}`)}
              <span className="ml-1 opacity-60">
                {featuresForTab(tabItem.id).length}
              </span>
            </button>
          ))}
        </div>

        <div className="grid flex-1 grid-cols-1 gap-x-8 gap-y-4 overflow-y-auto p-5 sm:grid-cols-2 lg:grid-cols-3">
          {list.map((f) => (
            <FeatureControl
              key={f.key}
              feature={f}
              value={uiValue(country, f, overrides)}
              pending={f.key in overrides}
              onChange={(v) => onDraft(f, v)}
            />
          ))}
        </div>

        <div className="flex flex-none items-center justify-end gap-2 border-t border-line px-5 py-3">
          {hasDrafts ? (
            <button
              onClick={onDiscard}
              className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:bg-elevated"
            >
              {t("world.discardDrafts")}
            </button>
          ) : null}
          <button
            onClick={onClose}
            className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:bg-elevated"
          >
            {t("common.close")}
          </button>
          <button
            onClick={onApply}
            disabled={!hasDrafts}
            className="rounded-lg bg-world px-5 py-2 text-sm font-semibold text-black hover:bg-emerald-400 disabled:opacity-40"
          >
            {t("world.apply")}
          </button>
        </div>
      </div>
    </div>
  );
}

/** Drag handle between world layout columns. */
function ResizeHandle({
  onResize,
  onResizeEnd,
  ariaLabel,
}: {
  onResize: (dx: number) => void;
  onResizeEnd?: () => void;
  ariaLabel: string;
}) {
  const lastX = useRef(0);

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      className="world-resize-handle group relative z-10 w-[5px] shrink-0"
      onPointerDown={(e) => {
        lastX.current = e.clientX;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
        const dx = e.clientX - lastX.current;
        if (dx !== 0) {
          lastX.current = e.clientX;
          onResize(dx);
        }
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        onResizeEnd?.();
      }}
    >
      <div className="absolute inset-y-0 -left-1 -right-1" />
    </div>
  );
}

/** Drag handle on the top edge of the downbar (row resize). */
function VerticalResizeHandle({
  onResize,
  onResizeEnd,
  ariaLabel,
}: {
  onResize: (dy: number) => void;
  onResizeEnd?: () => void;
  ariaLabel: string;
}) {
  const lastY = useRef(0);

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label={ariaLabel}
      className="world-resize-handle-h group relative z-10 h-[5px] shrink-0"
      onPointerDown={(e) => {
        lastY.current = e.clientY;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
        const dy = e.clientY - lastY.current;
        if (dy !== 0) {
          lastY.current = e.clientY;
          onResize(dy);
        }
      }}
      onPointerUp={(e) => {
        e.currentTarget.releasePointerCapture(e.pointerId);
        onResizeEnd?.();
      }}
    >
      <div className="absolute inset-x-0 -top-1 -bottom-1" />
    </div>
  );
}
