"use client";

import Link from "next/link";
import { useState, type ChangeEvent } from "react";
import { useLocale } from "@/contexts/LocaleContext";

type Row = {
  symbol: string;
  interval: string;
  decision_ms: number;
  first_forward_candle_end_ms: number;
  ai_correct: boolean | null;
  baseline_correct: boolean | null;
  ai_direction: number;
  baseline_direction: number;
  risk_veto: boolean;
  report_sha256: string;
  score_sha256: string;
};
type Metrics = {
  decisions: number;
  risk_veto: number;
  ai_scorable: number;
  ai_correct: number;
  ai_abstained_or_unscorable: number;
  ai_accuracy_conditional: number | null;
  ai_scorable_fraction: number | null;
  baseline_scorable: number;
  baseline_correct: number;
  paired_scorable: number;
  paired_ai_correct: number;
  paired_baseline_correct: number;
  paired_ai_accuracy: number | null;
  paired_baseline_accuracy: number | null;
  paired_difference_decimal: number | null;
  paired_ai_only_right: number;
  paired_baseline_only_right: number;
  paired_same_result: number;
};
type Cohort = {
  summary: Metrics;
  by_market: Record<string, Metrics>;
  decisions: Row[];
};
const schema = "econith-p1-longitudinal-direction-cohort-v1";
const verdict = "PASSED_REAUDITED_FINITE_DIRECTION_COHORT_ONLY";
const digestRe = /^[0-9a-f]{64}$/;

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}
function nonnegative(n: unknown): n is number {
  return typeof n === "number" && Number.isSafeInteger(n) && n >= 0;
}
function rate(n: unknown): n is number | null {
  return n === null || (typeof n === "number" && Number.isFinite(n) && n >= -1 && n <= 1);
}
function frac(num: number, den: number): number | null {
  return den ? num / den : null;
}
function matchesRatio(actual: number | null, expected: number | null): boolean {
  return actual === expected || (actual !== null && expected !== null &&
    Math.abs(actual - expected) <= 0.000002);
}
function validateMetrics(value: unknown, rows: Row[]): Metrics {
  const m = object(value);
  if (!m) throw new Error("Thiếu bảng thống kê.");
  const fields = [
    "decisions", "risk_veto", "ai_scorable", "ai_correct",
    "ai_abstained_or_unscorable", "baseline_scorable", "baseline_correct",
    "paired_scorable", "paired_ai_correct", "paired_baseline_correct",
    "paired_ai_only_right", "paired_baseline_only_right", "paired_same_result",
  ] as const;
  if (fields.some((key) => !nonnegative(m[key]))) {
    throw new Error("Bộ đếm trong báo cáo không hợp lệ.");
  }
  const ratioFields = [
    "ai_accuracy_conditional", "ai_scorable_fraction",
    "paired_ai_accuracy", "paired_baseline_accuracy", "paired_difference_decimal",
  ] as const;
  if (ratioFields.some((key) => !rate(m[key]))) {
    throw new Error("Tỷ lệ đánh giá không hợp lệ.");
  }
  const typed = m as unknown as Metrics;
  const ai = rows.filter((r) => r.ai_correct !== null);
  const baseline = rows.filter((r) => r.baseline_correct !== null);
  const paired = rows.filter((r) => r.ai_correct !== null && r.baseline_correct !== null);
  const a = ai.filter((r) => r.ai_correct === true).length;
  const b = baseline.filter((r) => r.baseline_correct === true).length;
  const pa = paired.filter((r) => r.ai_correct === true).length;
  const pb = paired.filter((r) => r.baseline_correct === true).length;
  if (typed.decisions !== rows.length ||
      typed.risk_veto !== rows.filter((r) => r.risk_veto).length ||
      typed.ai_scorable !== ai.length || typed.ai_correct !== a ||
      typed.ai_abstained_or_unscorable !== rows.length - ai.length ||
      typed.baseline_scorable !== baseline.length ||
      typed.baseline_correct !== b ||
      typed.paired_scorable !== paired.length ||
      typed.paired_ai_correct !== pa ||
      typed.paired_baseline_correct !== pb ||
      typed.paired_ai_only_right !== paired.filter((r) =>
        r.ai_correct === true && r.baseline_correct === false).length ||
      typed.paired_baseline_only_right !== paired.filter((r) =>
        r.ai_correct === false && r.baseline_correct === true).length ||
      typed.paired_same_result !== paired.filter((r) =>
        r.ai_correct === r.baseline_correct).length ||
      !matchesRatio(typed.ai_accuracy_conditional, frac(a, ai.length)) ||
      !matchesRatio(typed.ai_scorable_fraction, frac(ai.length, rows.length)) ||
      !matchesRatio(typed.paired_ai_accuracy, frac(pa, paired.length)) ||
      !matchesRatio(typed.paired_baseline_accuracy, frac(pb, paired.length)) ||
      !matchesRatio(typed.paired_difference_decimal, frac(pa - pb, paired.length))) {
    throw new Error("Bộ đếm hoặc tỷ lệ không khớp với các quyết định.");
  }
  return typed;
}
function validateCohort(value: unknown): Cohort {
  const root = object(value);
  if (!root || root.schema !== schema || root.verdict !== verdict ||
      root.scope !== "caller-selected-finite-originals-only" ||
      root.data_selection_complete_verified !== false ||
      root.independent_samples_certified !== false ||
      root.statistical_significance_claimed !== false ||
      root.realized_pnl_computed !== false ||
      root.source_receipts_signed !== false ||
      root.broker_access !== false || root.orders_submitted !== 0 ||
      !nonnegative(root.total_original_reaudits) ||
      !Array.isArray(root.decisions) || !root.decisions.length ||
      root.decisions.length > 96 ||
      root.total_original_reaudits !== root.decisions.length) {
    throw new Error("Không nhận diện được cohort nghiên cứu an toàn.");
  }
  const seen = new Set<string>();
  const outcomes = new Set<string>();
  const rows: Row[] = root.decisions.map((value: unknown) => {
    const r = object(value);
    if (!r ||
        !["BTCUSDT", "ETHUSDT", "BNBUSDT"].includes(String(r.symbol)) ||
        !["1m", "5m"].includes(String(r.interval)) ||
        !nonnegative(r.decision_ms) ||
        !nonnegative(r.first_forward_candle_end_ms) ||
        r.decision_ms >= r.first_forward_candle_end_ms ||
        ![-1, 0, 1].includes(r.ai_direction as number) ||
        ![-1, 0, 1].includes(r.baseline_direction as number) ||
        ![true, false, null].includes(r.ai_correct as null) ||
        ![true, false, null].includes(r.baseline_correct as null) ||
        typeof r.risk_veto !== "boolean" ||
        (r.risk_veto && (r.ai_direction !== 0 || r.ai_correct !== null)) ||
        (r.ai_direction === 0 && r.ai_correct !== null) ||
        (r.baseline_direction === 0 && r.baseline_correct !== null) ||
        typeof r.report_sha256 !== "string" || !digestRe.test(r.report_sha256) ||
        typeof r.score_sha256 !== "string" || !digestRe.test(r.score_sha256)) {
      throw new Error("Một quyết định thiếu trường hoặc mâu thuẫn trạng thái.");
    }
    const key = r.symbol + ":" + r.interval + ":" + r.decision_ms;
    const future = r.symbol + ":" + r.interval + ":" + r.first_forward_candle_end_ms;
    if (seen.has(key) || outcomes.has(future)) throw new Error("Phát hiện quyết định/nến lặp.");
    seen.add(key); outcomes.add(future);
    return r as Row;
  });
  const by = object(root.by_market);
  if (!by) throw new Error("Thiếu phân tách theo thị trường.");
  const expected = new Set(rows.map((r) => r.symbol + ":" + r.interval));
  if (Object.keys(by).length !== expected.size ||
      Object.keys(by).some((key) => !expected.has(key))) {
    throw new Error("Phân tách tài sản không nhất quán.");
  }
  const groups: Record<string, Metrics> = {};
  for (const key of expected) {
    const [symbol, interval] = key.split(":");
    groups[key] = validateMetrics(by[key],
      rows.filter((r) => r.symbol === symbol && r.interval === interval));
  }
  return { summary: validateMetrics(root.summary, rows), by_market: groups, decisions: rows };
}

function percent(value: number | null): string {
  return value === null ? "—" : (100 * value).toFixed(1) + "%";
}
function BarPair({ ai, baseline }: { ai: number | null; baseline: number | null }) {
  return (
    <div className="space-y-2" aria-label="Paired direction accuracy comparison">
      <div className="flex items-center gap-3">
        <span className="w-16 shrink-0 text-xs text-muted">AI</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-elevated">
          <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max(0, ai ?? 0) * 100}%` }} />
        </div>
        <span className="w-14 text-right font-mono text-xs">{percent(ai)}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="w-16 shrink-0 text-xs text-muted">Baseline</span>
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-elevated">
          <div className="h-full rounded-full bg-amber-500" style={{ width: `${Math.max(0, baseline ?? 0) * 100}%` }} />
        </div>
        <span className="w-14 text-right font-mono text-xs">{percent(baseline)}</span>
      </div>
    </div>
  );
}

export default function LongitudinalOverview() {
  const { locale } = useLocale();
  const en = locale === "en";
  const [cohort, setCohort] = useState<Cohort | null>(null);
  const [error, setError] = useState("");
  async function select(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    setCohort(null);
    setError("");
    if (!file) return;
    try {
      if (file.size === 0 || file.size > 512 * 1024) {
        throw new Error("File JSON phải có kích thước tối đa 512 KB.");
      }
      const parsed: unknown = JSON.parse(await file.text());
      setCohort(validateCohort(parsed));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể mở báo cáo.");
    }
  }
  const s = cohort?.summary;
  return (
    <main className="min-h-full bg-base px-4 py-10 text-ink sm:px-6">
      <div className="mx-auto max-w-7xl space-y-6">
        <Link href="/research" className="text-sm text-accent hover:underline">
          {en ? "← Evidence viewer" : "← Trở lại xem bằng chứng"}
        </Link>
        <header className="space-y-3">
          <p className="font-mono text-xs uppercase tracking-[.18em] text-accent">
            ECONITH / DIRECTION QUALITY RESEARCH
          </p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
            {en ? "Longitudinal research benchmark" : "Đánh giá dự báo qua nhiều quyết định"}
          </h1>
          <p className="max-w-3xl text-sm leading-7 text-muted">
            {en
              ? "Compare source-reaudited AI direction calls against the same-candle momentum baseline. Selected finite research only; no trading PnL."
              : "So sánh dự báo AI đã tái kiểm toán với baseline động lượng trên cùng nến kết quả. Đây là nghiên cứu hữu hạn do người vận hành chọn, không phải lợi nhuận giao dịch."}
          </p>
        </header>
        <section className="rounded-2xl border border-line bg-surface p-5">
          <p className="mb-3 text-sm leading-6 text-muted">
            {en
              ? "Choose research_quality_cohort.json produced by the offline Python auditor. This browser checks consistency only, not cryptographic provenance."
              : "Chọn research_quality_cohort.json do bộ kiểm toán Python ngoại tuyến tạo. Trình duyệt chỉ kiểm tra tính nhất quán, không xác thực nguồn bằng chữ ký số."}
          </p>
          <input type="file" accept=".json,application/json" onChange={select}
            aria-label="Select local research cohort JSON"
            className="block max-w-full text-sm text-muted file:mr-4 file:rounded-xl file:border-0 file:bg-accent file:px-4 file:py-2 file:font-semibold file:text-white" />
          {error && <p role="alert" className="mt-3 text-sm text-red-500">{error}</p>}
        </section>
        {s && cohort && (
          <>
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-5 py-4 text-sm leading-6 text-muted">
              {en
                ? "The cohort and receipt are unsigned and the operator chooses which decisions enter the sample. A matched direction is neither an executed trade nor proof of predictive edge. Correlated decisions cannot be treated as independent tests."
                : "Cohort chưa được ký số và người vận hành tự chọn quyết định đưa vào mẫu. Đúng hướng không đồng nghĩa giao dịch sinh lời. Các dự báo có tương quan không được coi là quan sát thống kê độc lập."}
            </div>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {([
                [en ? "Decisions" : "Quyết định", String(s.decisions)],
                [en ? "Scorable AI calls" : "Dự báo AI chấm được", String(s.ai_scorable) + "/" + s.decisions],
                [en ? "Paired comparison" : "Mẫu so sánh đồng thời", String(s.paired_scorable)],
                [en ? "Risk veto" : "Lần phủ quyết rủi ro", String(s.risk_veto)],
              ] as const).map(([label, value]) => (
                <div key={label} className="rounded-xl border border-line bg-surface p-5">
                  <p className="text-xs text-muted">{label}</p>
                  <p className="mt-2 font-mono text-2xl font-semibold">{value}</p>
                </div>
              ))}
            </div>
            <section className="rounded-2xl border border-line bg-surface p-6">
              <div className="mb-5 flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="text-lg font-semibold">{en ? "Fair paired comparison" : "So sánh trên cùng tập dự báo"}</h2>
                <span className="font-mono text-xs text-muted">
                  {en ? "AI minus baseline" : "Chênh lệch AI − baseline"}: {s.paired_difference_decimal === null
                    ? "—" : ((s.paired_difference_decimal * 100) > 0 ? "+" : "") +
                      (s.paired_difference_decimal * 100).toFixed(1) + " pp"}
                </span>
              </div>
              <BarPair ai={s.paired_ai_accuracy} baseline={s.paired_baseline_accuracy} />
              <p className="mt-5 text-xs leading-6 text-muted">
                {en
                  ? "Only decisions where both AI and baseline received a measurable non-flat outcome are included. No significance or tradable-alpha claim."
                  : "Chỉ tính các trường hợp cả AI và baseline đều có hướng dự báo và giá thay đổi khác 0. Không suy diễn ý nghĩa thống kê hay lợi thế giao dịch."}
              </p>
              <div className="mt-4 grid gap-2 text-xs text-muted sm:grid-cols-3">
                <span>AI-only correct: {s.paired_ai_only_right}</span>
                <span>Baseline-only correct: {s.paired_baseline_only_right}</span>
                <span>Same outcome: {s.paired_same_result}</span>
              </div>
            </section>
            <section className="space-y-3">
              <h2 className="text-lg font-semibold">{en ? "By market" : "Theo tài sản và khung nến"}</h2>
              <div className="grid gap-3 md:grid-cols-2">
                {Object.entries(cohort.by_market).map(([name, m]) => (
                  <article key={name} className="rounded-xl border border-line bg-surface p-5">
                    <div className="mb-4 flex items-center justify-between gap-3">
                      <h3 className="font-semibold">{name.replace(":", " · ")}</h3>
                      <span className="font-mono text-xs text-muted">n={m.paired_scorable}</span>
                    </div>
                    <BarPair ai={m.paired_ai_accuracy} baseline={m.paired_baseline_accuracy} />
                    <p className="mt-4 text-xs text-muted">{en ? "AI scorable fraction" : "Tỷ lệ AI chấm được"}: {percent(m.ai_scorable_fraction)}</p>
                  </article>
                ))}
              </div>
            </section>
            <section className="overflow-x-auto rounded-2xl border border-line bg-surface">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead className="border-b border-line text-xs text-muted">
                  <tr>
                    <th className="p-4">{en ? "Decision" : "Quyết định"}</th>
                    <th className="p-4">Market</th>
                    <th className="p-4">AI</th>
                    <th className="p-4">Baseline</th>
                    <th className="p-4">Risk</th>
                  </tr>
                </thead>
                <tbody>
                  {cohort.decisions.map((item) => (
                    <tr key={item.report_sha256} className="border-b border-line/60">
                      <td className="p-4 font-mono text-xs">{new Date(item.decision_ms).toLocaleString(en ? "en-US" : "vi-VN")}</td>
                      <td className="p-4">{item.symbol} · {item.interval}</td>
                      <td className="p-4">{item.ai_correct === null ? "—" : item.ai_correct ? "✓" : "✕"}</td>
                      <td className="p-4">{item.baseline_correct === null ? "—" : item.baseline_correct ? "✓" : "✕"}</td>
                      <td className="p-4">{item.risk_veto ? "VETO" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
