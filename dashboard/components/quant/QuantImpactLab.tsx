"use client";

import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowRight,
  faChartColumn,
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
    title: "World đã làm phần phân tích thay đổi thế nào?",
    subtitle:
      "Hai cột dùng cùng loại dữ liệu thị trường. Cột bên phải nhận thêm tín hiệu kinh tế từ World.",
    reality: "Chỉ dữ liệu thị trường",
    simulation: "Dữ liệu thị trường + World",
    awaitingReality: "Hãy chạy chế độ Chỉ dữ liệu thị trường để tạo mốc so sánh.",
    awaitingSimulation: "Bật chế độ Kết hợp World để xem phần chênh lệch.",
    risk: "Rủi ro ước tính",
    drawdown: "Mức giảm của danh mục",
    alerts: "Cảnh báo đang ghi nhận",
    price: "Giá thị trường khi ghi nhận",
    world: "WORLD",
    worldDesc: "Tình huống và trạng thái kinh tế",
    bridge: "CROSS IMPACT",
    bridgeDesc: "Chuyển thay đổi thành đầu vào định lượng",
    quant: "QUANT",
    quantDesc: "Cập nhật đánh giá dao động và rủi ro",
    active: "Đang truyền dữ liệu",
    inactive: "Đang ngắt kết nối",
    limit:
      "So sánh trực tiếp này cho thấy đường truyền kỹ thuật và mức thay đổi trong kịch bản hiện tại; không tự chứng minh dự báo tương lai chính xác hơn.",
    higher: "tăng",
    lower: "giảm",
    unchanged: "gần như không đổi",
    thanReality: "so với chỉ dùng dữ liệu thị trường",
  },
  en: {
    title: "How did World change market analysis?",
    subtitle:
      "Both columns use the same market-data type. The right column also receives World signals.",
    reality: "Market data only",
    simulation: "Market data + World",
    awaitingReality: "Run Market data only mode to capture a comparison baseline.",
    awaitingSimulation: "Enable World coupling to reveal the difference.",
    risk: "Estimated risk",
    drawdown: "Portfolio drawdown",
    alerts: "Recorded alerts",
    price: "Market price at capture",
    world: "WORLD",
    worldDesc: "Economic scenario and state",
    bridge: "CROSS IMPACT",
    bridgeDesc: "Transforms changes into quantitative inputs",
    quant: "QUANT",
    quantDesc: "Updates volatility and risk assessment",
    active: "Data is flowing",
    inactive: "Connection is gated",
    limit:
      "This live comparison demonstrates technical transmission and scenario deltas; it does not by itself prove better future forecasts.",
    higher: "higher",
    lower: "lower",
    unchanged: "nearly unchanged",
    thanReality: "than market data only",
  },
} as const;

function pct(value: number, digits = 2) {
  return `${(value * 100).toFixed(digits)}%`;
}

function money(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function QuantImpactComparison({
  reality,
  simulation,
  currentMode,
  locale,
  scenarioLabel,
}: {
  reality: QuantComparisonSample | null;
  simulation: QuantComparisonSample | null;
  currentMode: QuantModeName;
  locale: "vi" | "en";
  scenarioLabel?: string | null;
}) {
  const c = COPY[locale];
  const active = currentMode === "SIMULATION";
  const metrics = [
    { key: "risk", label: c.risk, format: (value: number) => pct(value) },
    { key: "drawdown", label: c.drawdown, format: (value: number) => pct(value) },
    { key: "alerts", label: c.alerts, format: (value: number) => value.toFixed(0) },
  ] as const;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-world">WORLD → QUANT</p>
          <h2 className="mt-1 text-xl font-bold text-ink">{c.title}</h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-muted">{c.subtitle}</p>
        </div>
        <span className={`rounded-full border px-3 py-1 text-[11px] font-semibold ${active ? "border-ok/40 bg-ok/10 text-ok" : "border-line bg-elevated text-muted"}`}>
          <span className={`mr-1.5 inline-block h-2 w-2 rounded-full ${active ? "animate-pulse bg-ok" : "bg-faint"}`} />
          {active ? c.active : c.inactive}
        </span>
      </div>

      <div className="mt-5 grid grid-cols-1 items-stretch gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr]">
        <FlowStep icon={faGlobe} title={c.world} text={scenarioLabel || c.worldDesc} active={active} />
        <FlowArrow active={active} />
        <FlowStep icon={faLanguage} title={c.bridge} text={c.bridgeDesc} active={active} />
        <FlowArrow active={active} />
        <FlowStep icon={faWaveSquare} title={c.quant} text={c.quantDesc} active={active} />
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <ComparisonColumn title={c.reality} sample={reality} empty={c.awaitingReality} metrics={metrics} priceLabel={c.price} />
        <ComparisonColumn title={c.simulation} sample={simulation} empty={c.awaitingSimulation} metrics={metrics} priceLabel={c.price} accent />
      </div>

      {reality && simulation ? (
        <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
          {metrics.map((metric) => {
            const before = reality[metric.key];
            const after = simulation[metric.key];
            const delta = after - before;
            const relative = Math.abs(before) > 1e-9 ? delta / Math.abs(before) : delta;
            const state = Math.abs(relative) < 0.005 ? c.unchanged : relative > 0 ? c.higher : c.lower;
            return (
              <div key={metric.key} className="rounded-xl border border-line bg-elevated/55 p-3">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-faint">{metric.label}</p>
                <div className="mt-2 flex items-end justify-between gap-3">
                  <MiniBars before={before} after={after} />
                  <p className={`text-right text-sm font-bold ${relative > 0.005 ? "text-danger" : relative < -0.005 ? "text-ok" : "text-muted"}`}>
                    {state}
                    <span className="block text-[10px] font-normal text-faint">{c.thanReality}</span>
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <p className="mt-5 rounded-xl border border-amber-300/50 bg-amber-100/60 px-3 py-2 text-[11px] leading-relaxed text-amber-900 dark:border-warn/30 dark:bg-warn/10 dark:text-warn">
        {c.limit}
      </p>
    </div>
  );
}

function FlowStep({ icon, title, text, active }: { icon: typeof faGlobe; title: string; text: string; active: boolean }) {
  return (
    <div className={`rounded-xl border p-3 transition ${active ? "border-world/50 bg-world/10 shadow-[0_0_24px_rgba(16,185,129,0.10)]" : "border-line bg-elevated/45"}`}>
      <div className="flex items-center gap-2">
        <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${active ? "bg-world text-black" : "bg-base text-faint"}`}>
          <FontAwesomeIcon icon={icon} className="h-3.5 w-3.5" />
        </span>
        <div>
          <p className="font-mono text-xs font-bold text-ink">{title}</p>
          <p className="mt-0.5 text-[10px] leading-snug text-muted">{text}</p>
        </div>
      </div>
    </div>
  );
}

function FlowArrow({ active }: { active: boolean }) {
  return (
    <div className={`flex items-center justify-center py-1 ${active ? "text-world" : "text-faint"}`}>
      <span className={`h-px w-5 ${active ? "bg-world" : "bg-line"}`} />
      <FontAwesomeIcon icon={faArrowRight} className={`h-3 w-3 ${active ? "animate-pulse" : ""}`} />
    </div>
  );
}

function ComparisonColumn({
  title,
  sample,
  empty,
  metrics,
  priceLabel,
  accent = false,
}: {
  title: string;
  sample: QuantComparisonSample | null;
  empty: string;
  metrics: ReadonlyArray<{ key: "risk" | "drawdown" | "alerts"; label: string; format: (value: number) => string }>;
  priceLabel: string;
  accent?: boolean;
}) {
  return (
    <section className={`rounded-2xl border p-4 ${accent ? "border-world/45 bg-world/5" : "border-line bg-elevated/35"}`}>
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-bold text-ink">{title}</h3>
        {sample ? <span className="font-mono text-[10px] text-muted">{sample.symbol}</span> : null}
      </div>
      {sample ? (
        <>
          <div className="mt-3 grid grid-cols-3 gap-2">
            {metrics.map((metric) => (
              <div key={metric.key} className="rounded-xl border border-line bg-surface p-3 text-center">
                <p className="text-[9px] font-semibold uppercase tracking-wide text-faint">{metric.label}</p>
                <p className="mt-1 font-mono text-lg font-bold text-ink">{metric.format(sample[metric.key])}</p>
              </div>
            ))}
          </div>
          <div className="mt-3 flex items-center justify-between border-t border-line pt-3 text-xs">
            <span className="text-muted">{priceLabel}</span>
            <span className="font-mono font-semibold text-ink">${money(sample.price)}</span>
          </div>
        </>
      ) : (
        <div className="mt-3 rounded-xl border border-dashed border-line px-4 py-8 text-center text-xs text-muted">{empty}</div>
      )}
    </section>
  );
}

function MiniBars({ before, after }: { before: number; after: number }) {
  const max = Math.max(Math.abs(before), Math.abs(after), 1e-6);
  return (
    <div className="flex h-12 items-end gap-1.5" aria-hidden="true">
      <span className="w-4 rounded-t bg-slate-400" style={{ height: `${Math.max(5, Math.abs(before / max) * 44)}px` }} />
      <span className="w-4 rounded-t bg-world" style={{ height: `${Math.max(5, Math.abs(after / max) * 44)}px` }} />
    </div>
  );
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
    <div className="min-h-0 flex-1 overflow-y-auto rounded-xl border border-line bg-surface p-4 sm:p-5">
      <div>
        <p className="font-mono text-[10px] font-semibold uppercase tracking-[0.2em] text-world">RQ1–RQ4</p>
        <h2 className="mt-1 text-xl font-bold text-ink">{vi ? "Kết quả kiểm định đã ghi trong báo cáo" : "Report-backed validation results"}</h2>
        <p className="mt-1 text-sm text-muted">{vi ? "Các số dưới đây là kết quả thí nghiệm lịch sử, không phải số được tạo từ thao tác giao diện hiện tại." : "These are recorded historical experiments, not values generated by the current UI interaction."}</p>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <EvidenceCard eyebrow="RQ1" title={vi ? "Vàng: dự báo sát hơn khoảng 3%" : "Gold: roughly 3% lower forecast error"} icon={faChartColumn}>
          <div className="grid grid-cols-2 gap-3">
            <BigResult value="−3,15%" label={vi ? "Sai lệch trung bình" : "Average error"} />
            <BigResult value="−3,49%" label={vi ? "Sai lệch lớn" : "Large-error measure"} />
          </div>
          <PairedErrorBars locale={locale} />
          <p className="mt-3 rounded-lg bg-amber-100 px-3 py-2 text-[11px] leading-snug text-amber-900 dark:bg-warn/10 dark:text-warn">
            {vi ? "Tín hiệu mới nổi bật ở vàng, 1 trong 11 tài sản. Chưa thể kết luận World luôn cải thiện dự báo." : "The signal was clear only for gold, 1 of 11 assets. This does not establish a general improvement."}
          </p>
        </EvidenceCard>

        <EvidenceCard eyebrow="RQ2" title={vi ? "World gần dữ liệu thực hơn sau hiệu chỉnh" : "World moved closer to observed data after calibration"} icon={faWaveSquare}>
          <div className="space-y-3">
            {calibration.map((item) => (
              <CalibrationRow key={item.label} {...item} />
            ))}
          </div>
          <p className="mt-3 text-[10px] leading-snug text-faint">{vi ? "Giá trị thấp hơn nghĩa là các đặc điểm được chọn gần dữ liệu dùng để hiệu chỉnh hơn." : "Lower values mean the selected characteristics are closer to the calibration data."}</p>
        </EvidenceCard>

        <EvidenceCard eyebrow="RQ3" title={vi ? "Đường truyền World → Quant hoạt động" : "The World → Quant pathway is active"} icon={faGlobe}>
          <div className="grid grid-cols-3 gap-2">
            <CheckTile label={vi ? "Mức dao động đổi" : "Volatility changed"} />
            <CheckTile label={vi ? "Mức giảm đổi" : "Drawdown changed"} />
            <CheckTile label={vi ? "Cảnh báo đổi" : "Alerts changed"} />
          </div>
          <p className="mt-3 text-[11px] leading-snug text-muted">{vi ? "Kết quả xác nhận phép ghép nối kỹ thuật; không khẳng định thị trường thật sẽ phản ứng giống mô phỏng." : "This validates the technical coupling, not identical real-market behaviour."}</p>
        </EvidenceCard>

        <EvidenceCard eyebrow="RQ4" title={vi ? "Chạy lại cho cùng kết quả" : "Repeated runs matched"} icon={faCircleCheck}>
          <div className="grid grid-cols-3 gap-2">
            <BigResult value="2" label={vi ? "lần chạy" : "runs"} compact />
            <BigResult value="11" label={vi ? "tài sản" : "assets"} compact />
            <BigResult value="100%" label={vi ? "trùng khớp" : "matched"} compact />
          </div>
          <p className="mt-3 text-[11px] leading-snug text-muted">{vi ? "Kết quả cho thấy tính lặp lại nội bộ khi dữ liệu và điều kiện được giữ nguyên." : "This demonstrates internal repeatability under identical data and conditions."}</p>
        </EvidenceCard>
      </div>
    </div>
  );
}

function EvidenceCard({ eyebrow, title, icon, children }: { eyebrow: string; title: string; icon: typeof faGlobe; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-line bg-elevated/35 p-4">
      <div className="mb-4 flex items-start gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-world/15 text-world"><FontAwesomeIcon icon={icon} className="h-4 w-4" /></span>
        <div><p className="font-mono text-[9px] font-bold tracking-[0.2em] text-world">{eyebrow}</p><h3 className="mt-0.5 text-sm font-bold text-ink">{title}</h3></div>
      </div>
      {children}
    </section>
  );
}

function BigResult({ value, label, compact = false }: { value: string; label: string; compact?: boolean }) {
  return (
    <div className="rounded-xl border border-world/25 bg-world/8 p-3 text-center">
      <p className={`font-mono font-bold text-world ${compact ? "text-2xl" : "text-3xl"}`}>{value}</p>
      <p className="mt-1 text-[10px] text-muted">{label}</p>
    </div>
  );
}

function PairedErrorBars({ locale }: { locale: "vi" | "en" }) {
  const marketOnly = locale === "vi" ? "Chỉ dữ liệu thị trường" : "Market only";
  const withWorld = locale === "vi" ? "Có thêm World" : "With World";
  return (
    <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
      {[
        { label: locale === "vi" ? "Sai lệch trung bình" : "Average error", after: 96.85 },
        { label: locale === "vi" ? "Sai lệch lớn" : "Large-error measure", after: 96.51 },
      ].map((item) => (
        <div key={item.label} className="rounded-lg border border-line bg-surface p-2.5">
          <p className="mb-2 text-[10px] font-semibold text-ink">{item.label}</p>
          <div className="grid grid-cols-[7.5rem_1fr_2.4rem] items-center gap-2 text-[9px] text-muted">
            <span>{marketOnly}</span><span className="h-2 rounded-full bg-slate-400" /><span className="font-mono text-right">100</span>
            <span>{withWorld}</span><span className="h-2 rounded-full bg-world" style={{ width: `${item.after}%` }} /><span className="font-mono text-right text-ok">{item.after.toFixed(2)}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

function CalibrationRow({ label, before, after }: { label: string; before: number; after: number }) {
  const max = Math.max(before, after, 0.001);
  return (
    <div>
      <div className="mb-1 flex items-center justify-between gap-3 text-[10px]"><span className="font-semibold text-ink">{label}</span><span className="font-mono text-muted">{before.toFixed(4)} → <strong className="text-ok">{after.toFixed(4)}</strong></span></div>
      <div className="space-y-1"><div className="h-1.5 rounded-full bg-slate-400" style={{ width: `${Math.max(3, (before / max) * 100)}%` }} /><div className="h-1.5 rounded-full bg-world" style={{ width: `${Math.max(3, (after / max) * 100)}%` }} /></div>
    </div>
  );
}

function CheckTile({ label }: { label: string }) {
  return <div className="rounded-xl border border-ok/25 bg-ok/8 p-3 text-center"><FontAwesomeIcon icon={faCircleCheck} className="h-4 w-4 text-ok" /><p className="mt-1.5 text-[10px] font-semibold leading-snug text-ink">{label}</p></div>;
}
