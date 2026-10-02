"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CountryMacro,
  WorldAgentEvent,
} from "@/hooks/useMetricsStream";
import {
  TITAN_NODES,
  TITAN_REGIONAL_CLUSTERS,
} from "@/constants/titanWorld";

type ImpactMetric = "risk" | "growth" | "inflation" | "credit";

interface NetworkNode {
  code: string;
  gdp: number;
  x: number;
  y: number;
  radius: number;
  region: string;
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

const WORLD_W = 1640;
const WORLD_H = 1080;
const EDGE_COUNT = 6000;

const COPY = {
  vi: {
    views: {
      risk: "Áp lực rủi ro",
      growth: "Tăng trưởng",
      inflation: "Lạm phát",
      credit: "Lãi suất & tín dụng",
    },
    drag: "Kéo để di chuyển · cuộn để thu phóng · bấm quốc gia để xem",
    nodes: "150 quốc gia",
    links: "6.000 mối liên kết",
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
    source: "Mạng được dựng từ quy mô GDP, liên minh và thuế quan trong trạng thái World.",
    scenario: "Tình huống đang quan sát",
  },
  en: {
    views: {
      risk: "Risk pressure",
      growth: "Growth",
      inflation: "Inflation",
      credit: "Rates & credit",
    },
    drag: "Drag to pan · scroll to zoom · click a country to inspect",
    nodes: "150 countries",
    links: "6,000 relationships",
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
    source: "The network uses GDP scale, alliances and tariffs in the current World state.",
    scenario: "Scenario being observed",
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
  if (metric === "growth") return growth;
  if (metric === "inflation") return -inflation;
  if (metric === "credit") return -rate;
  return growth * 0.45 - inflation * 0.25 - rate * 0.18 - unemployment * 0.12;
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
  const regionNames = Object.keys(TITAN_REGIONAL_CLUSTERS);
  const regionCenters = new Map<string, { x: number; y: number }>();
  regionNames.forEach((region, index) => {
    const angle = (index / regionNames.length) * Math.PI * 2 - Math.PI / 2;
    const ring = index % 3 === 0 ? 0.72 : index % 3 === 1 ? 0.5 : 0.32;
    regionCenters.set(region, {
      x: WORLD_W / 2 + Math.cos(angle) * WORLD_W * ring * 0.47,
      y: WORLD_H / 2 + Math.sin(angle) * WORLD_H * ring * 0.43,
    });
  });

  const gdps = TITAN_NODES.map((code) => Math.max(1e9, countries[code]?.gdp ?? 5e10));
  const minLog = Math.min(...gdps.map((gdp) => Math.log10(gdp)));
  const maxLog = Math.max(...gdps.map((gdp) => Math.log10(gdp)));
  const regionCounters = new Map<string, number>();
  const nodes: NetworkNode[] = TITAN_NODES.map((code, index) => {
    const region = regionByCode.get(code) ?? "Global";
    const center = regionCenters.get(region) ?? { x: WORLD_W / 2, y: WORLD_H / 2 };
    const localIndex = regionCounters.get(region) ?? 0;
    regionCounters.set(region, localIndex + 1);
    const angle = localIndex * 2.399963 + hash01(code) * 0.9;
    const radius = 30 + 43 * Math.sqrt(localIndex);
    const gdp = gdps[index];
    const scale = (Math.log10(gdp) - minLog) / Math.max(0.01, maxLog - minLog);
    return {
      code,
      gdp,
      region,
      x: clamp(center.x + Math.cos(angle) * radius, 45, WORLD_W - 45),
      y: clamp(center.y + Math.sin(angle) * radius, 45, WORLD_H - 45),
      radius: 12 + scale * 15,
    };
  });

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
}) {
  const copy = COPY[locale];
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const transformRef = useRef<Transform>({ x: 0, y: 0, scale: 0.72 });
  const [transform, setTransform] = useState<Transform>({ x: 0, y: 0, scale: 0.72 });
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [metric, setMetric] = useState<ImpactMetric>("risk");

  const graph = useMemo(
    () => makeGraph(countries, alliances, tariffs),
    [countries, alliances, tariffs],
  );
  const nodeByCode = useMemo(
    () => new Map(graph.nodes.map((node, index) => [node.code, { node, index }])),
    [graph.nodes],
  );

  const rawImpacts = useMemo(
    () => graph.nodes.map((node) => metricDelta(countries[node.code], baseline?.[node.code], metric)),
    [baseline, countries, graph.nodes, metric],
  );
  const maxImpact = Math.max(1e-8, ...rawImpacts.map((value) => Math.abs(value)));
  const impacts = rawImpacts.map((value) => clamp(value / maxImpact, -1, 1));

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
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = size.width * ratio;
    canvas.height = size.height * ratio;
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, size.width, size.height);
    ctx.save();
    ctx.translate(transform.x, transform.y);
    ctx.scale(transform.scale, transform.scale);

    graph.edges.forEach((edge) => {
      const a = graph.nodes[edge.source];
      const b = graph.nodes[edge.target];
      const impact = (impacts[edge.source] + impacts[edge.target]) / 2;
      const active = baseline ? Math.max(Math.abs(impacts[edge.source]), Math.abs(impacts[edge.target])) : 0;
      const alpha = baseline
        ? 0.025 + edge.weight * 0.045 + active * 0.22
        : 0.035 + edge.weight * 0.055;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = baseline
        ? rgbaForImpact(impact, alpha)
        : `rgba(71,85,105,${alpha})`;
      ctx.lineWidth = 0.45 + active * 1.4;
      ctx.stroke();
    });

    graph.nodes.forEach((node, index) => {
      const impact = impacts[index];
      const isSelected = node.code === selected;
      const isOrigin = node.code === origin;
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.radius, 0, Math.PI * 2);
      ctx.fillStyle = baseline ? rgbaForImpact(impact, 0.94) : "rgba(71,85,105,0.92)";
      if (isOrigin) ctx.fillStyle = "rgba(14,165,233,0.98)";
      ctx.fill();
      ctx.strokeStyle = isSelected ? "#f8fafc" : isOrigin ? "#38bdf8" : "rgba(255,255,255,0.72)";
      ctx.lineWidth = isSelected ? 3.2 : isOrigin ? 2.4 : 1;
      ctx.stroke();
      ctx.fillStyle = "#ffffff";
      ctx.font = `700 ${Math.max(8, Math.min(12, node.radius * 0.62))}px ui-monospace, SFMono-Regular, monospace`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(node.code, node.x, node.y + 0.5);
    });
    ctx.restore();
  }, [baseline, graph, impacts, origin, selected, size, transform]);

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

      <div className="pointer-events-none absolute left-3 top-3 flex flex-wrap gap-2">
        <span className="rounded-full border border-line bg-surface/92 px-3 py-1 font-mono text-[10px] font-semibold text-ink shadow-sm backdrop-blur">
          {copy.nodes}
        </span>
        <span className="rounded-full border border-line bg-surface/92 px-3 py-1 font-mono text-[10px] font-semibold text-ink shadow-sm backdrop-blur">
          {copy.links}
        </span>
      </div>

      {scenario ? (
        <div className="pointer-events-none absolute left-3 top-14 max-w-[min(23rem,calc(100%-1.5rem))] rounded-xl border border-sky-200 bg-white/94 px-3 py-2 shadow-md backdrop-blur dark:border-sky-800 dark:bg-slate-950/94">
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
      </div>

      {selectedNode ? (
        <aside className="absolute bottom-3 right-3 max-h-[68%] w-[min(21rem,calc(100%-1.5rem))] overflow-y-auto rounded-2xl border border-line bg-surface/96 p-4 shadow-xl backdrop-blur">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-world">{selected}</p>
              <h3 className="text-base font-bold text-ink">{countryName(selected, selectedCurrent?.name ?? selected)}</h3>
            </div>
            <span className="rounded-lg bg-elevated px-2 py-1 font-mono text-[10px] text-muted">
              GDP ${(selectedNode.gdp / 1e12).toFixed(2)}T
            </span>
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
