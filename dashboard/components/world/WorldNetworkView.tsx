"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faMinus, faPlus } from "@fortawesome/free-solid-svg-icons";
import type {
  CountryMacro,
  WorldAgentEvent,
} from "@/hooks/useMetricsStream";
import { ISO3_TO_NAME } from "@/constants/macroFeatures";
import {
  TITAN_NODES,
  TITAN_REGIONAL_CLUSTERS,
} from "@/constants/titanWorld";

type ImpactMetric = "risk" | "growth" | "inflation" | "credit" | "exports";

interface NetworkNode {
  code: string;
  gdp: number;
  x: number;
  y: number;
  radius: number;
  region: string;
  visualRegion: VisualRegionKey;
}

interface NetworkEdge {
  source: number;
  target: number;
  weight: number;
}

interface Transform {
  x: number;
  y: number;
  scale: number;
}

interface NodeActivity {
  strength: number;
  changedAt: number;
}

type VisualRegionKey =
  | "northAmerica"
  | "southAmerica"
  | "europe"
  | "asia"
  | "africaMiddleEast"
  | "oceania";

interface VisualRegion {
  key: VisualRegionKey;
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  accent: string;
  label: { vi: string; en: string };
}

const WORLD_W = 1800;
const WORLD_H = 1080;
const EDGE_COUNT = 6000;
const VISUAL_REGIONS: readonly VisualRegion[] = [
  { key: "northAmerica", centerX: 285, centerY: 285, radiusX: 235, radiusY: 185, accent: "#38bdf8", label: { vi: "BẮC MỸ & CARIBE", en: "NORTH AMERICA & CARIBBEAN" } },
  { key: "europe", centerX: 765, centerY: 250, radiusX: 245, radiusY: 180, accent: "#8b5cf6", label: { vi: "CHÂU ÂU", en: "EUROPE" } },
  { key: "asia", centerX: 1370, centerY: 350, radiusX: 335, radiusY: 245, accent: "#f59e0b", label: { vi: "CHÂU Á & Á-ÂU", en: "ASIA & EURASIA" } },
  { key: "southAmerica", centerX: 330, centerY: 790, radiusX: 215, radiusY: 205, accent: "#10b981", label: { vi: "NAM MỸ", en: "SOUTH AMERICA" } },
  { key: "africaMiddleEast", centerX: 815, centerY: 775, radiusX: 265, radiusY: 210, accent: "#f97316", label: { vi: "CHÂU PHI & TRUNG ĐÔNG", en: "AFRICA & MIDDLE EAST" } },
  { key: "oceania", centerX: 1450, centerY: 815, radiusX: 275, radiusY: 175, accent: "#ec4899", label: { vi: "CHÂU ĐẠI DƯƠNG", en: "OCEANIA" } },
] as const;

const EUROPE_FALLBACK = new Set(["BIH", "ALB", "MKD", "MLT", "CYP"]);
const OCEANIA_FALLBACK = new Set(["KIR", "MHL", "PLW", "FSM", "NRU", "TUV", "ASM", "GUM"]);

function visualRegionFor(code: string, region: string): VisualRegionKey {
  if (region === "NorthAmerica" || region === "Caribbean") return "northAmerica";
  if (region === "SouthAmerica") return "southAmerica";
  if (region.includes("Europe") || EUROPE_FALLBACK.has(code)) return "europe";
  if (region === "MENA" || region === "SubSaharanAfrica") return "africaMiddleEast";
  if (region === "Oceania" || OCEANIA_FALLBACK.has(code)) return "oceania";
  return "asia";
}

const COPY = {
  vi: {
    views: {
      risk: "Áp lực rủi ro",
      growth: "Tăng trưởng",
      inflation: "Lạm phát",
      credit: "Lãi suất & tín dụng",
      exports: "Xuất khẩu",
    },
    drag: "Kéo để di chuyển · cuộn để thu phóng · bấm quốc gia để xem",
    nodes: "150 quốc gia",
    links: "6.000 đường liên kết mô hình",
    waiting: "Chưa có tình huống so sánh",
    waitingDesc:
      "Điều chỉnh một chỉ số ở bảng bên trái rồi bấm Áp dụng. Mạng sẽ so sánh trạng thái trước và sau.",
    active: "Đang hiển thị tác động của",
    beforeAfter: "Thay đổi trước → sau",
    growth: "Tăng trưởng",
    inflation: "Lạm phát",
    rate: "Lãi suất",
    unemployment: "Thất nghiệp",
    agents: "Phản ứng gần nhất trong quốc gia",
    noAgents: "Chưa có phản ứng tác nhân mới cho quốc gia này.",
    legendHigher: "Áp lực tăng",
    legendLower: "Áp lực giảm",
    legendNone: "Chưa thay đổi rõ",
    source: "Các đường nối là lớp trực quan hóa được dựng từ quy mô GDP, liên minh và thuế quan; màu và nhịp sáng lấy từ thay đổi thật của World.",
    scenario: "Tình huống đang quan sát",
    running: "World đang xử lý liên tục",
    paused: "World đang dừng",
    day: "Ngày",
    activeNodes: "quốc gia vừa thay đổi",
    regionHint: "Các quốc gia được xếp theo khu vực để dễ theo dõi đường lan truyền.",
    collapse: "Thu nhỏ bảng quốc gia",
    expand: "Mở bảng quốc gia",
  },
  en: {
    views: {
      risk: "Risk pressure",
      growth: "Growth",
      inflation: "Inflation",
      credit: "Rates & credit",
      exports: "Exports",
    },
    drag: "Drag to pan · scroll to zoom · click a country to inspect",
    nodes: "150 countries",
    links: "6,000 model links",
    waiting: "No comparison scenario yet",
    waitingDesc:
      "Adjust a metric in the left panel and press Apply. The network will compare the before and after states.",
    active: "Showing impact from",
    beforeAfter: "Before → after",
    growth: "Growth",
    inflation: "Inflation",
    rate: "Policy rate",
    unemployment: "Unemployment",
    agents: "Latest agent responses in this country",
    noAgents: "No recent agent response for this country.",
    legendHigher: "Pressure increased",
    legendLower: "Pressure decreased",
    legendNone: "No clear change",
    source: "Links visualise GDP scale, alliances and tariffs; colours and pulses come from actual World state changes.",
    scenario: "Scenario being observed",
    running: "World is processing continuously",
    paused: "World is paused",
    day: "Day",
    activeNodes: "countries just changed",
    regionHint: "Countries are grouped by region so propagation paths are easier to follow.",
    collapse: "Collapse country panel",
    expand: "Open country panel",
  },
} as const;

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function hash01(value: string) {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967295;
}

function readMetric(country: CountryMacro | undefined, key: string) {
  if (!country) return 0;
  if (key === "growth") return country.gdp_growth ?? 0;
  if (key === "inflation") return country.inflation ?? country.vectors?.monetary?.inflation_cpi ?? 0;
  if (key === "rate") return country.interest_rate ?? country.vectors?.monetary?.interest_rate ?? 0;
  if (key === "unemployment") return country.unemployment ?? country.vectors?.labor?.unemployment ?? 0;
  if (key === "exports") return country.vectors?.fiscal?.export_index ?? 0;
  if (key === "tradeBalance") return country.vectors?.fiscal?.trade_balance_pct ?? 0;
  return 0;
}

function metricDelta(
  current: CountryMacro | undefined,
  baseline: CountryMacro | undefined,
  metric: ImpactMetric,
) {
  if (!current || !baseline) return 0;
  const growth = readMetric(current, "growth") - readMetric(baseline, "growth");
  const inflation = readMetric(current, "inflation") - readMetric(baseline, "inflation");
  const rate = readMetric(current, "rate") - readMetric(baseline, "rate");
  const unemployment =
    readMetric(current, "unemployment") - readMetric(baseline, "unemployment");
  // Export index and trade balance are the channels through which the World
  // engine models tariff pressure and supply-chain diversion. Scale them to a
  // fraction-like range so they can be read beside the macro deltas.
  const exports =
    (readMetric(current, "exports") - readMetric(baseline, "exports")) / 100;
  const tradeBalance =
    readMetric(current, "tradeBalance") - readMetric(baseline, "tradeBalance");
  if (metric === "growth") return growth;
  if (metric === "inflation") return -inflation;
  if (metric === "credit") return -rate;
  if (metric === "exports") return exports;
  return (
    growth * 0.35 -
    inflation * 0.22 -
    rate * 0.15 -
    unemployment * 0.1 +
    exports * 0.13 +
    tradeBalance * 0.05
  );
}

function fmtPct(value: number) {
  const sign = value > 0 ? "+" : "";
  return `${sign}${(value * 100).toFixed(2)}%`;
}

function rgbaForImpact(value: number, alpha = 1) {
  if (Math.abs(value) < 0.035) return `rgba(100,116,139,${alpha})`;
  if (value > 0) return `rgba(16,185,129,${alpha})`;
  const strength = Math.min(1, Math.abs(value));
  return strength > 0.58
    ? `rgba(239,68,68,${alpha})`
    : `rgba(249,115,22,${alpha})`;
}

function makeGraph(
  countries: Record<string, CountryMacro>,
  alliances: Record<string, Record<string, number>>,
  tariffs: Record<string, Record<string, number>>,
) {
  const regionByCode = new Map<string, string>();
  Object.entries(TITAN_REGIONAL_CLUSTERS).forEach(([region, codes]) => {
    codes.forEach((code) => regionByCode.set(code, region));
  });
  const gdps = TITAN_NODES.map((code) => Math.max(1e9, countries[code]?.gdp ?? 5e10));
  const minLog = Math.min(...gdps.map((gdp) => Math.log10(gdp)));
  const maxLog = Math.max(...gdps.map((gdp) => Math.log10(gdp)));
  const nodeMeta = TITAN_NODES.map((code, index) => {
    const region = regionByCode.get(code) ?? "Global";
    const gdp = gdps[index];
    const scale = (Math.log10(gdp) - minLog) / Math.max(0.01, maxLog - minLog);
    return {
      code,
      gdp,
      region,
      visualRegion: visualRegionFor(code, region),
      radius: 11 + scale * 12,
    };
  });

  const placements = new Map<string, { x: number; y: number; radius: number }>();
  const anchors = new Map<string, { x: number; y: number }>();
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  VISUAL_REGIONS.forEach((visualRegion) => {
    const members = nodeMeta
      .filter((node) => node.visualRegion === visualRegion.key)
      .sort((a, b) => b.gdp - a.gdp || a.code.localeCompare(b.code));
    members.forEach((node, index) => {
      const spread = Math.sqrt((index + 0.35) / Math.max(1, members.length));
      const angle = index * goldenAngle + hash01(`${node.code}:cluster`) * 0.7;
      const x = visualRegion.centerX + Math.cos(angle) * visualRegion.radiusX * 0.72 * spread;
      const y = visualRegion.centerY + Math.sin(angle) * visualRegion.radiusY * 0.72 * spread;
      placements.set(node.code, { x, y, radius: node.radius });
      anchors.set(node.code, { x, y });
    });
  });

  const nodes: NetworkNode[] = nodeMeta.map((node) => {
    const placement = placements.get(node.code) ?? { x: WORLD_W / 2, y: WORLD_H / 2, radius: node.radius };
    return { ...node, ...placement };
  });

  // Preserve the free, organic network shape while keeping each continental
  // cloud readable. A weak pull keeps nodes near their regional anchor and a
  // deterministic collision pass prevents circles from covering each other.
  for (let iteration = 0; iteration < 140; iteration += 1) {
    VISUAL_REGIONS.forEach((visualRegion) => {
      const members = nodes.filter((node) => node.visualRegion === visualRegion.key);
      members.forEach((node) => {
        const anchor = anchors.get(node.code);
        if (anchor) {
          node.x += (anchor.x - node.x) * 0.018;
          node.y += (anchor.y - node.y) * 0.018;
        }
      });
      for (let index = 0; index < members.length; index += 1) {
        const node = members[index];
        for (let otherIndex = index + 1; otherIndex < members.length; otherIndex += 1) {
          const other = members[otherIndex];
          let dx = other.x - node.x;
          let dy = other.y - node.y;
          let distance = Math.hypot(dx, dy);
          const minimum = node.radius + other.radius + 7;
          if (distance >= minimum) continue;
          if (distance < 0.001) {
            const angle = hash01(`${node.code}:${other.code}`) * Math.PI * 2;
            dx = Math.cos(angle);
            dy = Math.sin(angle);
            distance = 1;
          }
          const push = (minimum - distance) * 0.52;
          const ux = dx / distance;
          const uy = dy / distance;
          node.x -= ux * push;
          node.y -= uy * push;
          other.x += ux * push;
          other.y += uy * push;
        }
      }
      members.forEach((node) => {
        const safeX = Math.max(40, visualRegion.radiusX - node.radius - 18);
        const safeY = Math.max(40, visualRegion.radiusY - node.radius - 18);
        const dx = node.x - visualRegion.centerX;
        const dy = node.y - visualRegion.centerY;
        const ellipseDistance = Math.sqrt((dx * dx) / (safeX * safeX) + (dy * dy) / (safeY * safeY));
        if (ellipseDistance > 1) {
          node.x = visualRegion.centerX + dx / ellipseDistance;
          node.y = visualRegion.centerY + dy / ellipseDistance;
        }
      });
    });
  }

  const maxGdp = Math.max(...gdps);
  const candidates: NetworkEdge[] = [];
  for (let source = 0; source < nodes.length; source += 1) {
    for (let target = source + 1; target < nodes.length; target += 1) {
      const a = nodes[source];
      const b = nodes[target];
      const alliance =
        ((alliances[a.code]?.[b.code] ?? 0.5) +
          (alliances[b.code]?.[a.code] ?? 0.5)) /
        2;
      const tariff =
        ((tariffs[a.code]?.[b.code] ?? 0.03) +
          (tariffs[b.code]?.[a.code] ?? 0.03)) /
        2;
      const gravity = Math.sqrt(a.gdp * b.gdp) / maxGdp;
      const regional = a.region === b.region ? 0.13 : 0;
      const noise = hash01(`${a.code}:${b.code}`) * 0.055;
      candidates.push({
        source,
        target,
        weight: clamp(0.42 * alliance + 0.19 * tariff + 0.31 * gravity + regional + noise, 0, 1),
      });
    }
  }
  candidates.sort((a, b) => b.weight - a.weight);
  return { nodes, edges: candidates.slice(0, EDGE_COUNT) };
}

export function WorldNetworkView({
  countries,
  alliances = {},
  tariffs = {},
  baseline,
  origin,
  scenario,
  selected,
  onSelect,
  agents = [],
  locale,
  countryName,
  running,
  simDay,
}: {
  countries: Record<string, CountryMacro>;
  alliances?: Record<string, Record<string, number>>;
  tariffs?: Record<string, Record<string, number>>;
  baseline: Record<string, CountryMacro> | null;
  origin: string | null;
  scenario: { code: string; label: string; before: string; after: string } | null;
  selected: string;
  onSelect: (code: string) => void;
  agents?: WorldAgentEvent[];
  locale: "en" | "vi";
  countryName: (code: string, fallback?: string) => string;
  running: boolean;
  simDay: number;
}) {
  const copy = COPY[locale];
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const transformRef = useRef<Transform>({ x: 0, y: 0, scale: 0.72 });
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, scale: 0.72 });
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [metric, setMetric] = useState<ImpactMetric>("risk");
  const [activeNodeCount, setActiveNodeCount] = useState(0);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const graphLockedRef = useRef(Object.keys(countries).length > 0);
  const [graph, setGraph] = useState(() => makeGraph(countries, alliances, tariffs));
  const previousMetricsRef = useRef<Map<string, [number, number, number, number]>>(new Map());
  const activityRef = useRef<Map<string, NodeActivity>>(new Map());

  useEffect(() => {
    if (graphLockedRef.current || Object.keys(countries).length === 0) return;
    setGraph(makeGraph(countries, alliances, tariffs));
    graphLockedRef.current = true;
  }, [alliances, countries, tariffs]);
  const nodeByCode = useMemo(
    () => new Map(graph.nodes.map((node, index) => [node.code, { node, index }])),
    [graph.nodes],
  );

  const rawImpacts = useMemo(
    () => graph.nodes.map((node) => metricDelta(countries[node.code], baseline?.[node.code], metric)),
    [baseline, countries, graph.nodes, metric],
  );
  const impacts = useMemo(() => {
    const maxImpact = Math.max(1e-8, ...rawImpacts.map((value) => Math.abs(value)));
    return rawImpacts.map((value) => clamp(value / maxImpact, -1, 1));
  }, [rawImpacts]);

  useEffect(() => {
    const previous = previousMetricsRef.current;
    const next = new Map<string, [number, number, number, number]>();
    const now = performance.now();
    let changedCount = 0;
    graph.nodes.forEach((node) => {
      const country = countries[node.code];
      const values: [number, number, number, number] = [
        readMetric(country, "growth"),
        readMetric(country, "inflation"),
        readMetric(country, "rate"),
        readMetric(country, "unemployment"),
      ];
      next.set(node.code, values);
      const before = previous.get(node.code);
      if (!before) return;
      const movement = values.reduce(
        (sum, value, index) => sum + Math.abs(value - before[index]),
        0,
      );
      if (movement > 1e-8) {
        changedCount += 1;
        activityRef.current.set(node.code, {
          strength: clamp(0.22 + movement * 180, 0.22, 1),
          changedAt: now,
        });
      }
    });
    previousMetricsRef.current = next;
    setActiveNodeCount(changedCount);
  }, [countries, graph.nodes, simDay]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(([entry]) => {
      const width = Math.floor(entry.contentRect.width);
      const height = Math.floor(entry.contentRect.height);
      setSize({ width, height });
      if (transformRef.current.x === 0 && transformRef.current.y === 0) {
        const scale = Math.min(width / WORLD_W, height / WORLD_H) * 0.92;
        const next = {
          scale,
          x: (width - WORLD_W * scale) / 2,
          y: (height - WORLD_H * scale) / 2,
        };
        transformRef.current = next;
        setTransform(next);
      }
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !size.width || !size.height) return;
    const ratio = Math.min(1.5, window.devicePixelRatio || 1);
    canvas.width = size.width * ratio;
    canvas.height = size.height * ratio;
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const staticLayer = document.createElement("canvas");
    staticLayer.width = WORLD_W;
    staticLayer.height = WORLD_H;
    const staticCtx = staticLayer.getContext("2d");
    if (!staticCtx) return;
    const dark = document.documentElement.classList.contains("dark");

    VISUAL_REGIONS.forEach((region) => {
      staticCtx.save();
      staticCtx.beginPath();
      staticCtx.ellipse(
        region.centerX,
        region.centerY,
        region.radiusX,
        region.radiusY,
        -0.04,
        0,
        Math.PI * 2,
      );
      staticCtx.globalAlpha = dark ? 0.11 : 0.065;
      staticCtx.fillStyle = region.accent;
      staticCtx.fill();
      staticCtx.globalAlpha = dark ? 0.42 : 0.28;
      staticCtx.strokeStyle = region.accent;
      staticCtx.lineWidth = 1.6;
      staticCtx.setLineDash([7, 10]);
      staticCtx.stroke();
      staticCtx.setLineDash([]);
      staticCtx.globalAlpha = 1;
      staticCtx.beginPath();
      staticCtx.arc(
        region.centerX - region.radiusX + 18,
        region.centerY - region.radiusY + 20,
        5,
        0,
        Math.PI * 2,
      );
      staticCtx.fillStyle = region.accent;
      staticCtx.fill();
      staticCtx.fillStyle = dark ? "rgba(203,213,225,0.92)" : "rgba(51,65,85,0.92)";
      staticCtx.font = "700 16px ui-sans-serif, system-ui, sans-serif";
      staticCtx.textAlign = "left";
      staticCtx.textBaseline = "middle";
      staticCtx.fillText(
        region.label[locale],
        region.centerX - region.radiusX + 31,
        region.centerY - region.radiusY + 20,
      );
      staticCtx.restore();
    });

    // All 6,000 relationships remain visible, but they are rasterised once as
    // a quiet context layer. Only links carrying a recent backend state change
    // are animated in the live layer below.
    graph.edges.forEach((edge) => {
      const a = graph.nodes[edge.source];
      const b = graph.nodes[edge.target];
      const impact = (impacts[edge.source] + impacts[edge.target]) / 2;
      staticCtx.beginPath();
      staticCtx.moveTo(a.x, a.y);
      staticCtx.lineTo(b.x, b.y);
      staticCtx.strokeStyle = baseline
        ? rgbaForImpact(impact, 0.018 + edge.weight * 0.025)
        : dark
          ? `rgba(148,163,184,${0.012 + edge.weight * 0.022})`
          : `rgba(71,85,105,${0.009 + edge.weight * 0.018})`;
      staticCtx.lineWidth = 0.48 + edge.weight * 0.24;
      staticCtx.stroke();
    });

    let animationFrame = 0;
    let previousFrame = 0;
    const draw = (now: number) => {
      if (running && now - previousFrame < 32) {
        animationFrame = window.requestAnimationFrame(draw);
        return;
      }
      previousFrame = now;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, size.width, size.height);
      ctx.save();
      ctx.translate(transform.x, transform.y);
      ctx.scale(transform.scale, transform.scale);
      ctx.drawImage(staticLayer, 0, 0);

      const activity = graph.nodes.map((node) => {
        const signal = activityRef.current.get(node.code);
        if (!signal) return 0;
        const age = now - signal.changedAt;
        return age >= 1800 ? 0 : signal.strength * (1 - age / 1800);
      });

      let particleCount = 0;
      graph.edges.forEach((edge, edgeIndex) => {
        const a = graph.nodes[edge.source];
        const b = graph.nodes[edge.target];
        const impact = (impacts[edge.source] + impacts[edge.target]) / 2;
        const live = Math.max(activity[edge.source], activity[edge.target]);
        const compared = baseline
          ? Math.max(Math.abs(impacts[edge.source]), Math.abs(impacts[edge.target]))
          : 0;
        const active = Math.max(live, compared);
        if (active < 0.08 || edgeIndex % 11 !== 0) return;
        const alpha = 0.08 + active * 0.26;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = baseline ? rgbaForImpact(impact, alpha) : `rgba(14,165,233,${alpha})`;
        ctx.lineWidth = 0.7 + active * 1.15;
        ctx.stroke();

        // A moving bead is rendered only on a deterministic subset of active
        // links. Its existence is driven by a real state delta; animation merely
        // makes the latest backend tick legible to the human eye.
        if (running && live > 0.16 && particleCount < 24 && edgeIndex % 33 === 0) {
          particleCount += 1;
          const progress = (now * 0.00045 * (1 + edge.weight) + edgeIndex * 0.071) % 1;
          const x = a.x + (b.x - a.x) * progress;
          const y = a.y + (b.y - a.y) * progress;
          ctx.beginPath();
          ctx.arc(x, y, 1.6 + live * 1.8, 0, Math.PI * 2);
          ctx.fillStyle = baseline
            ? rgbaForImpact(impact, 0.82)
            : "rgba(14,165,233,0.84)";
          ctx.fill();
        }
      });

      graph.nodes.forEach((node, index) => {
        const impact = impacts[index];
        const live = activity[index];
        const isSelected = node.code === selected;
        const isOrigin = node.code === origin;
        if (running && live > 0.08) {
          const pulse = (Math.sin(now * 0.006 + index) + 1) / 2;
          ctx.beginPath();
          ctx.arc(node.x, node.y, node.radius + 3 + pulse * 5 * live, 0, Math.PI * 2);
          ctx.fillStyle = baseline
            ? rgbaForImpact(impact, 0.06 + live * 0.13)
            : `rgba(14,165,233,${0.05 + live * 0.13})`;
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
        ctx.fillStyle = baseline ? rgbaForImpact(impact, 0.94) : "rgba(71,85,105,0.94)";
        if (isOrigin) ctx.fillStyle = "rgba(14,165,233,0.98)";
        ctx.fill();
        ctx.strokeStyle = isSelected ? "#f8fafc" : isOrigin ? "#38bdf8" : "rgba(255,255,255,0.76)";
        ctx.lineWidth = isSelected ? 3.2 : isOrigin ? 2.4 : 1;
        ctx.stroke();
        ctx.fillStyle = "#ffffff";
        ctx.font = `700 ${Math.max(8, Math.min(12, node.radius * 0.6))}px ui-monospace, SFMono-Regular, monospace`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        ctx.fillText(node.code, node.x, node.y + 0.5);
      });
      ctx.restore();
      if (running) animationFrame = window.requestAnimationFrame(draw);
    };
    draw(performance.now());
    return () => window.cancelAnimationFrame(animationFrame);
  }, [baseline, graph, impacts, locale, origin, running, selected, size, transform]);

  const findNode = useCallback(
    (clientX: number, clientY: number) => {
      const rect = canvasRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const worldX = (clientX - rect.left - transformRef.current.x) / transformRef.current.scale;
      const worldY = (clientY - rect.top - transformRef.current.y) / transformRef.current.scale;
      let match: NetworkNode | undefined;
      let distance = Infinity;
      for (const node of graph.nodes) {
        const d = Math.hypot(worldX - node.x, worldY - node.y);
        if (d <= node.radius + 5 && d < distance) {
          match = node;
          distance = d;
        }
      }
      return match ?? null;
    },
    [graph.nodes],
  );

  const selectedNode = nodeByCode.get(selected)?.node;
  const selectedCurrent = countries[selected];
  const selectedBaseline = baseline?.[selected];
  const selectedAgents = agents.filter((event) => event.country === selected).slice(0, 4);
  const selectedFullName = countryName(
    selected,
    ISO3_TO_NAME[selected] ?? selectedCurrent?.name ?? selected,
  );
  return (
    <div ref={hostRef} className="absolute inset-0 overflow-hidden bg-[#f8fafc] dark:bg-[#090b10]">
      <canvas
        ref={canvasRef}
        className="h-full w-full cursor-grab touch-none active:cursor-grabbing"
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = { x: event.clientX, y: event.clientY, moved: false };
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag) return;
          const dx = event.clientX - drag.x;
          const dy = event.clientY - drag.y;
          if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
          drag.x = event.clientX;
          drag.y = event.clientY;
          const next = {
            ...transformRef.current,
            x: transformRef.current.x + dx,
            y: transformRef.current.y + dy,
          };
          transformRef.current = next;
          setTransform(next);
        }}
        onPointerUp={(event) => {
          const drag = dragRef.current;
          dragRef.current = null;
          if (drag && !drag.moved) {
            const node = findNode(event.clientX, event.clientY);
            if (node) onSelect(node.code);
          }
        }}
        onWheel={(event) => {
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          const pointerX = event.clientX - rect.left;
          const pointerY = event.clientY - rect.top;
          const previous = transformRef.current;
          const nextScale = clamp(previous.scale * Math.exp(-event.deltaY * 0.0012), 0.25, 2.5);
          const worldX = (pointerX - previous.x) / previous.scale;
          const worldY = (pointerY - previous.y) / previous.scale;
          const next = {
            scale: nextScale,
            x: pointerX - worldX * nextScale,
            y: pointerY - worldY * nextScale,
          };
          transformRef.current = next;
          setTransform(next);
        }}
      />

      <div className="pointer-events-none absolute left-3 top-3 flex w-[18rem] max-w-[calc(100%-1.5rem)] flex-wrap gap-2">
        <span className="rounded-full border border-line bg-surface/92 px-3 py-1 font-mono text-[10px] font-semibold text-ink shadow-sm backdrop-blur">
          {copy.nodes}
        </span>
        <span className="rounded-full border border-line bg-surface/92 px-3 py-1 font-mono text-[10px] font-semibold text-ink shadow-sm backdrop-blur">
          {copy.links}
        </span>
        <span className={`basis-full rounded-full border px-3 py-1 font-mono text-[10px] font-semibold shadow-sm backdrop-blur ${running ? "border-ok/35 bg-ok/10 text-ok" : "border-line bg-surface/92 text-muted"}`}>
          <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${running ? "animate-pulse bg-ok" : "bg-faint"}`} />
          {running ? copy.running : copy.paused} · {copy.day} {simDay.toLocaleString()} · {activeNodeCount} {copy.activeNodes}
        </span>
      </div>

      {scenario ? (
        <div className="pointer-events-none absolute left-3 top-[5.25rem] max-w-[min(23rem,calc(100%-1.5rem))] rounded-xl border border-sky-200 bg-white/94 px-3 py-2 shadow-md backdrop-blur dark:border-sky-800 dark:bg-slate-950/94">
          <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-sky-700 dark:text-sky-300">{copy.scenario}</p>
          <p className="mt-0.5 text-xs font-bold text-ink">{scenario.code} · {scenario.label}</p>
          <p className="mt-1 font-mono text-[11px] text-muted">{scenario.before} → <strong className="text-sky-600 dark:text-sky-300">{scenario.after}</strong></p>
        </div>
      ) : null}

      <div className="absolute right-3 top-28 flex max-w-[calc(100%-1.5rem)] flex-wrap justify-end gap-1 rounded-xl border border-line bg-surface/94 p-1.5 shadow-md backdrop-blur sm:top-16">
        {(Object.keys(copy.views) as ImpactMetric[]).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setMetric(key)}
            className={`rounded-lg px-2.5 py-1 text-[10px] font-semibold transition ${
              metric === key ? "bg-world text-black" : "text-muted hover:bg-elevated hover:text-ink"
            }`}
          >
            {copy.views[key]}
          </button>
        ))}
      </div>

      <div className="pointer-events-none absolute bottom-3 left-3 max-w-[36rem] rounded-xl border border-line bg-surface/92 px-3 py-2 shadow-sm backdrop-blur">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
          <LegendDot color="#10b981" label={copy.legendLower} />
          <LegendDot color="#f97316" label={copy.legendHigher} />
          <LegendDot color="#64748b" label={copy.legendNone} />
        </div>
        <p className="mt-1 text-[10px] text-faint">{baseline && origin ? `${copy.active} ${origin}. ` : ""}{copy.drag}</p>
        <p className="mt-0.5 text-[9px] text-faint">{copy.regionHint}</p>
      </div>

      {selectedNode && inspectorCollapsed ? (
        <button
          type="button"
          onClick={() => setInspectorCollapsed(false)}
          className="absolute bottom-3 right-3 flex max-w-[min(22rem,calc(100%-1.5rem))] items-center gap-2 rounded-xl border border-line bg-surface/96 px-3 py-2 text-left shadow-lg backdrop-blur transition hover:border-world/50"
          aria-label={copy.expand}
          title={copy.expand}
        >
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-world/15 text-world">
            <FontAwesomeIcon icon={faPlus} className="h-3 w-3" />
          </span>
          <span className="min-w-0">
            <span className="block truncate text-xs font-bold text-ink">{selectedFullName}</span>
            <span className="block font-mono text-[9px] text-faint">{selected} · {copy.expand}</span>
          </span>
        </button>
      ) : selectedNode ? (
        <aside className="absolute bottom-3 right-3 max-h-[68%] w-[min(21rem,calc(100%-1.5rem))] overflow-y-auto rounded-2xl border border-line bg-surface/96 p-4 shadow-xl backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-world">{selected}</p>
              <h3 className="text-base font-bold text-ink">{selectedFullName}</h3>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="rounded-lg bg-elevated px-2 py-1 font-mono text-[10px] text-muted">
                GDP ${(selectedNode.gdp / 1e12).toFixed(2)}T
              </span>
              <button
                type="button"
                onClick={() => setInspectorCollapsed(true)}
                className="flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-elevated text-muted transition hover:border-world/50 hover:text-ink"
                aria-label={copy.collapse}
                title={copy.collapse}
              >
                <FontAwesomeIcon icon={faMinus} className="h-3 w-3" />
              </button>
            </div>
          </div>

          {selectedBaseline ? (
            <div className="mt-3">
              <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">{copy.beforeAfter}</p>
              <div className="grid grid-cols-2 gap-2">
                <DeltaCard label={copy.growth} value={fmtPct(readMetric(selectedCurrent, "growth") - readMetric(selectedBaseline, "growth"))} />
                <DeltaCard label={copy.inflation} value={fmtPct(readMetric(selectedCurrent, "inflation") - readMetric(selectedBaseline, "inflation"))} inverse />
                <DeltaCard label={copy.rate} value={fmtPct(readMetric(selectedCurrent, "rate") - readMetric(selectedBaseline, "rate"))} inverse />
                <DeltaCard label={copy.unemployment} value={fmtPct(readMetric(selectedCurrent, "unemployment") - readMetric(selectedBaseline, "unemployment"))} inverse />
              </div>
            </div>
          ) : (
            <div className="mt-3 rounded-xl border border-dashed border-line bg-elevated/50 p-3">
              <p className="text-xs font-semibold text-ink">{copy.waiting}</p>
              <p className="mt-1 text-[11px] leading-snug text-muted">{copy.waitingDesc}</p>
            </div>
          )}

          <div className="mt-3 border-t border-line pt-3">
            <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-faint">{copy.agents}</p>
            {selectedAgents.length ? (
              <div className="space-y-2">
                {selectedAgents.map((event) => (
                  <div key={`${event.ts}-${event.actor}`} className="rounded-lg bg-elevated/70 px-2.5 py-2">
                    <p className="text-[10px] font-semibold text-world">{event.actor}</p>
                    <p className="mt-0.5 text-[11px] leading-snug text-muted">{locale === "vi" ? event.text_vi || event.text : event.text}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[11px] text-muted">{copy.noAgents}</p>
            )}
          </div>
          <p className="mt-3 border-t border-line pt-2 text-[9px] leading-snug text-faint">{copy.source}</p>
        </aside>
      ) : null}
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

function DeltaCard({ label, value, inverse = false }: { label: string; value: string; inverse?: boolean }) {
  const numeric = Number(value.replace("%", ""));
  const positive = inverse ? numeric <= 0 : numeric >= 0;
  return (
    <div className="rounded-lg border border-line bg-elevated/60 p-2">
      <p className="text-[9px] uppercase tracking-wide text-faint">{label}</p>
      <p className={`mt-0.5 font-mono text-sm font-bold ${Math.abs(numeric) < 0.001 ? "text-muted" : positive ? "text-ok" : "text-danger"}`}>{value}</p>
    </div>
  );
}
