"use client";

import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowRight,
  faCircleCheck,
  faGlobe,
  faLanguage,
  faWaveSquare,
} from "@fortawesome/free-solid-svg-icons";
import type { QuantModeName } from "@/hooks/useMetricsStream";

export interface QuantComparisonSample {
  mode: QuantModeName;
  symbol: string;
  capturedAt: string;
  risk: number;
  drawdown: number;
  alerts: number;
  price: number;
}

const COPY = {
  vi: {
    title: "World đang thay đổi phần phân tích thế nào?",
    subtitle: "Theo dõi đường đi của thông tin và đặt các chỉ số trước – sau cạnh nhau.",
    reality: "Chỉ dữ liệu thị trường",
    simulation: "Dữ liệu thị trường + World",
    awaitingReality: "Chuyển sang chế độ Chỉ dữ liệu thị trường để ghi mốc ban đầu.",
    awaitingSimulation: "Chuyển sang chế độ Kết hợp World để ghi trạng thái có mô phỏng.",
    risk: "Rủi ro ước tính",
    drawdown: "Mức giảm của danh mục",
    alerts: "Cảnh báo ghi nhận",
    price: "Giá lúc ghi nhận",
    world: "WORLD",
    worldDesc: "Tình huống kinh tế đang chạy",
    bridge: "CROSS IMPACT",
    bridgeDesc: "Chuyển thay đổi thành tín hiệu số",
    quant: "QUANT",
    quantDesc: "Cập nhật mức dao động và rủi ro",
    active: "Đường truyền đang hoạt động",
    inactive: "World đang được ngắt khỏi Quant",
    limit: "Phần này cho thấy tín hiệu đã đi qua hệ thống và các chỉ số đã thay đổi. Việc dự báo có tốt hơn hay không được đánh giá riêng trong Kết quả kiểm định.",
    noChange: "Không đổi rõ",
    captured: "Ghi nhận",
    difference: "Chênh lệch",
    higher: "Tăng",
    lower: "Giảm",
    unchanged: "Gần như không đổi",
    versus: "so với khi chỉ dùng dữ liệu thị trường",
  },
  en: {
    title: "How is World changing the analysis?",
    subtitle: "Follow the data path and compare before-and-after metrics side by side.",
    reality: "Market data only",
    simulation: "Market data + World",
    awaitingReality: "Switch to Market data only to capture a baseline.",
    awaitingSimulation: "Switch to Market + World to capture the coupled state.",
    risk: "Estimated risk",
    drawdown: "Portfolio drawdown",
    alerts: "Recorded warnings",
    price: "Price at capture",
    world: "WORLD",
    worldDesc: "Current economic scenario",
    bridge: "CROSS IMPACT",
    bridgeDesc: "Turns changes into numeric signals",
    quant: "QUANT",
    quantDesc: "Updates volatility and risk",
    active: "Data pathway is active",
    inactive: "World is disconnected from Quant",
    limit: "This view shows technical transmission and metric changes. Forecast improvement is evaluated separately under Validated results.",
    noChange: "No clear change",
    captured: "Captured",
    difference: "Difference",
    higher: "Higher",
    lower: "Lower",
    unchanged: "Nearly unchanged",
    versus: "versus market data only",
  },
} as const;

function pct(value: number, digits = 2) {
  return `${(value * 100).toFixed(digits)}%`;
}

function money(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function captureTime(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function QuantImpactComparison({
  reality,
  simulation,
  currentMode,
  couplingEnabled,
  locale,
  scenarioLabel,
}: {
  reality: QuantComparisonSample | null;
  simulation: QuantComparisonSample | null;
  currentMode: QuantModeName;
  /** Authoritative backend state: only true when World signals enter Quant. */
  couplingEnabled: boolean;
  locale: "vi" | "en";
  scenarioLabel?: string | null;
}) {
  const c = COPY[locale];
  // Do not infer the bridge from the selected tab/mode alone.  The backend
  // exposes an explicit gate, which is also what prevents World data leaking
  // into a "market data only" run.
  const active = currentMode === "SIMULATION" && couplingEnabled;
  const metrics = [
    { key: "risk", label: c.risk, format: (value: number) => pct(value) },
    { key: "drawdown", label: c.drawdown, format: (value: number) => pct(value) },
    { key: "alerts", label: c.alerts, format: (value: number) => value.toFixed(0) },
  ] as const;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-line bg-surface p-4 sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.22em] text-world">WORLD → CROSS IMPACT → QUANT</p>
          <h2 className="mt-1 text-2xl font-bold text-ink sm:text-3xl">{c.title}</h2>
          <p className="mt-2 max-w-3xl text-base leading-relaxed text-muted">{c.subtitle}</p>
        </div>
        <span className={`rounded-full border px-3 py-1.5 text-[11px] font-semibold ${active ? "border-ok/40 bg-ok/10 text-ok" : "border-line bg-elevated text-muted"}`}>
          <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${active ? "animate-pulse bg-ok" : "bg-faint"}`} />
          {active ? c.active : c.inactive}
        </span>
      </div>

      <section className="mt-5 overflow-hidden rounded-2xl border border-world/25 bg-gradient-to-r from-world/10 via-surface to-zone-alpha/10">
        <div className="border-b border-line px-4 py-3">
          <p className="text-[9px] font-semibold uppercase tracking-[0.18em] text-faint">{locale === "vi" ? "Tình huống đang theo dõi" : "Scenario being observed"}</p>
          <p className="mt-1 text-sm font-bold text-ink">{scenarioLabel || c.worldDesc}</p>
        </div>
        <div className="grid grid-cols-1 items-stretch md:grid-cols-[1fr_auto_1fr_auto_1fr]">
          <FlowStep icon={faGlobe} title={c.world} text={scenarioLabel || c.worldDesc} active={active} order="01" />
          <FlowArrow active={active} />
          <FlowStep icon={faLanguage} title={c.bridge} text={c.bridgeDesc} active={active} order="02" />
          <FlowArrow active={active} />
          <FlowStep icon={faWaveSquare} title={c.quant} text={c.quantDesc} active={active} order="03" />
        </div>
      </section>

      <div className="mt-6 grid grid-cols-1 gap-5 xl:grid-cols-2">
        <ComparisonColumn title={c.reality} sample={reality} empty={c.awaitingReality} metrics={metrics} priceLabel={c.price} />
        <ComparisonColumn title={c.simulation} sample={simulation} empty={c.awaitingSimulation} metrics={metrics} priceLabel={c.price} accent />
      </div>

      {reality && simulation ? (
        <section className="mt-5 rounded-2xl border border-line bg-elevated/30 p-4 sm:p-5">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h3 className="text-lg font-bold text-ink">{locale === "vi" ? "World đã làm các chỉ số thay đổi bao nhiêu?" : "How much did World change each metric?"}</h3>
            <span className="rounded-full border border-line bg-surface px-3 py-1 text-xs font-semibold text-muted">{c.difference}</span>
          </div>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {metrics.map((metric) => (
              <ImpactDeltaCard
                key={metric.key}
                label={metric.label}
                before={reality[metric.key]}
                after={simulation[metric.key]}
                format={metric.format}
                higher={c.higher}
                lower={c.lower}
                unchanged={c.unchanged}
                versus={c.versus}
              />
            ))}
          </div>
        </section>
      ) : null}
      <p className="mt-5 rounded-xl border border-amber-300/50 bg-amber-100/60 px-4 py-3 text-sm leading-relaxed text-amber-900 dark:border-warn/30 dark:bg-warn/10 dark:text-warn">{c.limit}</p>
    </div>
  );
}

function FlowStep({ icon, title, text, active, order }: { icon: typeof faGlobe; title: string; text: string; active: boolean; order: string }) {
  return <div className="relative flex min-h-32 items-center gap-4 p-5"><span className="absolute right-3 top-2 font-mono text-3xl font-black text-faint/20">{order}</span><span className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border ${active ? "border-world/40 bg-world text-black shadow-[0_0_28px_rgba(16,185,129,.2)]" : "border-line bg-elevated text-faint"}`}><FontAwesomeIcon icon={icon} className="h-5 w-5" /></span><div className="min-w-0"><p className="font-mono text-sm font-bold text-ink">{title}</p><p className="mt-1 text-sm leading-snug text-muted">{text}</p></div></div>;
}

function FlowArrow({ active }: { active: boolean }) {
  return <div className={`hidden items-center md:flex ${active ? "text-world" : "text-faint"}`}><span className={`h-px w-6 ${active ? "bg-world" : "bg-line"}`} /><FontAwesomeIcon icon={faArrowRight} className={`h-3 w-3 ${active ? "animate-pulse" : ""}`} /></div>;
}

function ComparisonColumn({ title, sample, empty, metrics, priceLabel, accent = false }: { title: string; sample: QuantComparisonSample | null; empty: string; metrics: ReadonlyArray<{ key: "risk" | "drawdown" | "alerts"; label: string; format: (value: number) => string }>; priceLabel: string; accent?: boolean }) {
  return <section className={`rounded-2xl border p-5 sm:p-6 ${accent ? "border-world/50 bg-world/5 shadow-[0_12px_40px_rgba(16,185,129,.08)]" : "border-line bg-elevated/40"}`}><div className="flex items-start justify-between gap-3"><div><h3 className="text-lg font-bold text-ink sm:text-xl">{title}</h3><p className="mt-1 font-mono text-xs text-muted">{sample ? `${sample.symbol} · ${captureTime(sample.capturedAt)}` : "—"}</p></div>{accent ? <span className="rounded-full bg-world px-3 py-1 text-xs font-bold text-black">+ WORLD</span> : null}</div>{sample ? <><div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">{metrics.map((metric) => <div key={metric.key} className="rounded-xl border border-line bg-surface p-4 text-center"><p className="min-h-10 text-xs font-semibold uppercase leading-snug tracking-wide text-muted">{metric.label}</p><p className={`mt-2 font-mono text-2xl font-black sm:text-3xl ${accent ? "text-world" : "text-ink"}`}>{metric.format(sample[metric.key])}</p></div>)}</div><div className="mt-4 flex items-center justify-between border-t border-line pt-4"><span className="text-sm text-muted">{priceLabel}</span><span className="font-mono text-xl font-bold text-ink">${money(sample.price)}</span></div></> : <div className="mt-4 rounded-xl border border-dashed border-line px-5 py-10 text-center text-sm leading-relaxed text-muted">{empty}</div>}</section>;
}

function ImpactDeltaCard({ label, before, after, format, higher, lower, unchanged, versus }: { label: string; before: number; after: number; format: (value: number) => string; higher: string; lower: string; unchanged: string; versus: string }) {
  const delta = after - before;
  const relative = Math.abs(before) > 1e-9 ? delta / Math.abs(before) : delta;
  const neutral = Math.abs(relative) < 0.005;
  const state = neutral ? unchanged : delta > 0 ? higher : lower;
  return <div className="rounded-xl border border-line bg-surface p-4"><p className="text-sm font-bold text-ink">{label}</p><div className="mt-3 flex items-end justify-between gap-3"><MiniBars before={before} after={after} /><div className="text-right"><p className={`text-xl font-black ${neutral ? "text-muted" : delta > 0 ? "text-danger" : "text-ok"}`}>{state}</p><p className="mt-0.5 font-mono text-sm font-bold text-ink">{neutral ? "≈ 0" : `${delta > 0 ? "+" : ""}${format(delta)}`}</p></div></div><p className="mt-3 text-xs text-muted">{versus}</p></div>;
}

function MiniBars({ before, after }: { before: number; after: number }) {
  const max = Math.max(Math.abs(before), Math.abs(after), 1e-6);
  return <div className="flex h-16 items-end gap-2" aria-hidden="true"><span className="w-6 rounded-t-md bg-slate-400" style={{ height: `${Math.max(8, Math.abs(before / max) * 60)}px` }} /><span className="w-6 rounded-t-md bg-world" style={{ height: `${Math.max(8, Math.abs(after / max) * 60)}px` }} /></div>;
}

export function ResearchEvidence({ locale }: { locale: "vi" | "en" }) {
  const vi = locale === "vi";
  const calibration = [
    { label: vi ? "Lãi suất" : "Policy rate", before: 0.8555, after: 0.2425 },
    { label: vi ? "Lợi suất trái phiếu 10 năm" : "10Y bond yield", before: 0.7503, after: 0.0108 },
    { label: vi ? "Tăng trưởng GDP" : "GDP growth", before: 0.071, after: 0.011 },
    { label: "CPI", before: 1.31, after: 1.1419 },
  ];
  return (
    <div className="min-h-0 flex-1 overflow-y-auto rounded-2xl border border-line bg-surface p-5 sm:p-7">
      <div className="flex flex-wrap items-end justify-between gap-3"><div><p className="font-mono text-xs font-semibold uppercase tracking-[0.22em] text-world">RQ1–RQ4</p><h2 className="mt-1 text-2xl font-bold text-ink sm:text-3xl">{vi ? "Bốn kết quả chính của nghiên cứu" : "Four core research results"}</h2><p className="mt-2 text-base text-muted">{vi ? "Số liệu được khóa theo các thí nghiệm đã ghi trong báo cáo." : "Values are locked to the experiments recorded in the report."}</p></div><span className="rounded-full border border-line bg-elevated px-4 py-2 font-mono text-xs font-semibold text-muted">{vi ? "11 loại tài sản được kiểm tra" : "11 tested assets"}</span></div>

      <section className="mt-5 overflow-hidden rounded-2xl border border-world/30 bg-gradient-to-br from-world/12 via-surface to-surface">
        <div className="grid grid-cols-1 xl:grid-cols-[.78fr_1.22fr]">
          <div className="border-b border-line p-6 xl:border-b-0 xl:border-r"><p className="font-mono text-xs font-bold tracking-[0.2em] text-world">RQ1 · GLD</p><h3 className="mt-2 text-2xl font-bold text-ink">{vi ? "Vàng là trường hợp có tín hiệu cải thiện" : "Gold showed an improvement signal"}</h3><div className="mt-5 grid grid-cols-2 gap-4"><BigResult value="−3,15%" label={vi ? "sai lệch trung bình" : "average error"} /><BigResult value="−3,49%" label={vi ? "sai lệch lớn" : "large-error measure"} /></div><p className="mt-4 rounded-xl border border-amber-300/40 bg-amber-100/60 px-4 py-3 text-sm leading-relaxed text-amber-900 dark:bg-warn/10 dark:text-warn">{vi ? "Kết quả nổi bật ở 1 trong 11 tài sản; chưa thể kết luận World luôn cải thiện dự báo." : "The result was clear in 1 of 11 assets; it does not establish a general improvement."}</p></div>
          <div className="p-6"><p className="text-xs font-semibold uppercase tracking-wider text-faint">{vi ? "Mức sai số tương đối · mốc ban đầu = 100" : "Relative error · baseline = 100"}</p><div className="mt-6 space-y-7"><ErrorComparison label={vi ? "Sai lệch trung bình" : "Average error"} after={96.85} locale={locale} /><ErrorComparison label={vi ? "Sai lệch lớn" : "Large-error measure"} after={96.51} locale={locale} /></div></div>
        </div>
      </section>

      <section className="mt-5 rounded-2xl border border-line bg-elevated/30 p-5 sm:p-6"><div className="flex items-center gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-xl bg-world/15 text-world"><FontAwesomeIcon icon={faWaveSquare} className="h-5 w-5" /></span><div><p className="font-mono text-xs font-bold tracking-[.2em] text-world">RQ2</p><h3 className="text-lg font-bold text-ink">{vi ? "Khoảng cách với dữ liệu thực đều giảm sau hiệu chỉnh" : "Distance to observed data fell after calibration"}</h3></div></div><div className="mt-5 grid grid-cols-1 gap-4 md:grid-cols-2">{calibration.map((item) => <CalibrationRow key={item.label} {...item} vi={vi} />)}</div><p className="mt-4 text-sm leading-relaxed text-muted">{vi ? "Chỉ số thấp hơn nghĩa là các đặc điểm được chọn gần dữ liệu dùng để hiệu chỉnh hơn." : "A lower value means the selected characteristics are closer to the calibration data."}</p></section>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <section className="rounded-2xl border border-line bg-elevated/30 p-5 sm:p-6"><div className="flex items-center gap-3"><span className="flex h-11 w-11 items-center justify-center rounded-xl bg-sky-500/10 text-sky-600"><FontAwesomeIcon icon={faGlobe} className="h-5 w-5" /></span><div><p className="font-mono text-xs font-bold tracking-[.2em] text-sky-600">RQ3</p><h3 className="text-lg font-bold text-ink">{vi ? "Tín hiệu đi được từ World sang Quant" : "Signals travelled from World to Quant"}</h3></div></div><div className="mt-5 grid grid-cols-3 gap-3"><CheckTile label={vi ? "Mức dao động đổi" : "Volatility changed"} /><CheckTile label={vi ? "Mức giảm đổi" : "Drawdown changed"} /><CheckTile label={vi ? "Cảnh báo đổi" : "Warnings changed"} /></div><p className="mt-4 text-sm leading-relaxed text-muted">{vi ? "Kết quả xác nhận đường truyền kỹ thuật, không khẳng định thị trường thật sẽ phản ứng giống mô phỏng." : "This confirms the technical pathway, not identical real-market behaviour."}</p></section>
        <section className="relative overflow-hidden rounded-2xl border border-ok/25 bg-ok/5 p-4 sm:p-5"><div className="absolute -right-8 -top-10 h-32 w-32 rounded-full bg-ok/10" /><div className="relative flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-xl bg-ok/15 text-ok"><FontAwesomeIcon icon={faCircleCheck} className="h-4 w-4" /></span><div><p className="font-mono text-[9px] font-bold tracking-[.2em] text-ok">RQ4</p><h3 className="text-sm font-bold text-ink">{vi ? "Chạy lại cho cùng kết quả" : "Repeated runs matched"}</h3></div></div><div className="relative mt-5 grid grid-cols-3 divide-x divide-line rounded-xl border border-line bg-surface"><Stat value="2" label={vi ? "lần chạy" : "runs"} /><Stat value="11" label={vi ? "tài sản" : "assets"} /><Stat value="100%" label={vi ? "trùng khớp" : "matched"} /></div><p className="relative mt-3 text-[11px] leading-relaxed text-muted">{vi ? "Tính lặp lại nội bộ được xác nhận khi dữ liệu và điều kiện được giữ nguyên." : "Internal repeatability was confirmed under identical data and conditions."}</p></section>
      </div>
    </div>
  );
}

function ErrorComparison({ label, after, locale }: { label: string; after: number; locale: "vi" | "en" }) {
  const improvement = 100 - after;
  return <div><div className="mb-3 flex items-end justify-between gap-3"><p className="text-base font-semibold text-ink">{label}</p><p className="font-mono text-lg font-bold text-ok">−{improvement.toFixed(2).replace(".", locale === "vi" ? "," : ".")}%</p></div><div className="space-y-3"><BarRow label={locale === "vi" ? "Chỉ thị trường" : "Market only"} value={100} color="bg-slate-400" /><BarRow label={locale === "vi" ? "Có World" : "With World"} value={after} color="bg-world" /></div></div>;
}

function BarRow({ label, value, color }: { label: string; value: number; color: string }) {
  return <div className="grid grid-cols-[8rem_1fr_4rem] items-center gap-3 text-sm"><span className="text-muted">{label}</span><span className="h-4 overflow-hidden rounded-full bg-base"><span className={`block h-full rounded-full ${color}`} style={{ width: `${value}%` }} /></span><strong className="text-right font-mono text-base text-ink">{value.toFixed(2)}</strong></div>;
}

function BigResult({ value, label }: { value: string; label: string }) {
  return <div className="rounded-xl border border-world/25 bg-surface/80 p-4 text-center shadow-sm"><p className="font-mono text-4xl font-black text-world">{value}</p><p className="mt-2 text-sm font-semibold text-muted">{label}</p></div>;
}

function CalibrationRow({ label, before, after, vi }: { label: string; before: number; after: number; vi: boolean }) {
  const max = Math.max(before, after, 0.001);
  const drop = ((before - after) / before) * 100;
  return <div className="rounded-xl border border-line bg-surface p-4"><div className="flex items-center justify-between gap-3"><span className="text-sm font-semibold text-ink">{label}</span><span className="rounded-full bg-ok/10 px-2.5 py-1 font-mono text-xs font-bold text-ok">−{drop.toFixed(1).replace(".", vi ? "," : ".")}%</span></div><div className="mt-4 space-y-2.5"><div className="flex items-center gap-3"><span className="w-16 text-xs text-faint">{vi ? "Trước" : "Before"}</span><span className="h-3 rounded-full bg-slate-400" style={{ width: `${Math.max(4, (before / max) * 75)}%` }} /><span className="font-mono text-xs text-muted">{before.toFixed(4)}</span></div><div className="flex items-center gap-3"><span className="w-16 text-xs text-faint">{vi ? "Sau" : "After"}</span><span className="h-3 rounded-full bg-world" style={{ width: `${Math.max(4, (after / max) * 75)}%` }} /><span className="font-mono text-xs font-bold text-world">{after.toFixed(4)}</span></div></div></div>;
}

function CheckTile({ label }: { label: string }) {
  return <div className="rounded-xl border border-ok/25 bg-ok/10 p-4 text-center"><FontAwesomeIcon icon={faCircleCheck} className="h-5 w-5 text-ok" /><p className="mt-2 text-sm font-semibold leading-snug text-ink">{label}</p></div>;
}

function Stat({ value, label }: { value: string; label: string }) {
  return <div className="p-4 text-center"><p className="font-mono text-3xl font-black text-ok sm:text-4xl">{value}</p><p className="mt-1 text-xs font-semibold uppercase tracking-wide text-muted">{label}</p></div>;
}
