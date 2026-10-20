"use client";

import { useRef, useState, type ChangeEvent } from "react";
import Link from "next/link";
import { useLocale } from "@/contexts/LocaleContext";

type RecordValue = Record<string, unknown>;
type Analyst = { role: string; stance: string; rationale: string };
type LoadedPanel = {
  type: "panel";
  name: string;
  model: string;
  verdict: string;
  rating: string;
  veto: boolean;
  reports: Analyst[];
  inputTokens: number;
  outputTokens: number;
  timestamp: number | null;
  evidence: string;
};
type LoadedScore = {
  type: "scorecard";
  name: string;
  symbol: string;
  interval: string;
  direction: number;
  baseline: number;
  result: boolean | null;
  baselineResult: boolean | null;
  rawReturn: string;
  rating: string;
  sourceEnd: number;
  outcomeEnd: number;
  evidence: string;
};
type ResearchReport = LoadedPanel | LoadedScore;
type AuditReceipt = {
  kind: "panel" | "scorecard";
  report_sha256: string;
  evidence_sha256: string;
  verdict: string;
};

const receiptSchema = "econith-p1-local-research-audit-receipt-v1";
const receiptVerdict = "PASSED_LOCAL_PYTHON_ORIGINAL_REAUDIT_ONLY";
const receiptMode = "offline-original-source-recomputation";

const ROLES = ["technical", "bull", "bear", "risk", "portfolio"] as const;
const schemaPanel = "econith-p1-openai-panel-optional-research-v1";
const schemaScore = "econith-p1-pit-advisory-forward-close-quality-v1";

function record(v: unknown): RecordValue | null {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as RecordValue)
    : null;
}
function word(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
function digest(v: unknown): string {
  return typeof v === "string" && /^[0-9a-f]{64}$/.test(v) ? v : "";
}
function readReport(value: unknown, filename: string): ResearchReport {
  const root = record(value);
  if (!root || root.orders_submitted !== 0) {
    throw new Error("Báo cáo không đúng cấu trúc hoặc có trường lệnh giao dịch.");
  }
  if (root.schema === schemaPanel) {
    const advisory = record(root.advisory);
    const evidence = record(root.evidence);
    const reports = root.reports;
    if (
      root.verdict !== "PASSED_BOUNDED_LLM_ADVISORY_ONLY" ||
      root.model_calls !== 5 ||
      !advisory ||
      advisory.advisory_only !== true ||
      advisory.can_place_orders !== false ||
      advisory.orders_submitted !== 0 ||
      !evidence ||
      !digest(evidence.evidence_sha256) ||
      !Array.isArray(reports) ||
      reports.length !== 5
    ) {
      throw new Error("Không phải bản ghi analyst hợp lệ theo schema ECONITH.");
    }
    const parsed = reports.map((item: unknown) => {
      const r = record(item);
      if (!r || !ROLES.includes(word(r.role) as (typeof ROLES)[number]) ||
          !word(r.stance) || !word(r.rationale) ||
          r.evidence_sha256 !== evidence.evidence_sha256) {
        throw new Error("Thiếu vai trò hoặc phiếu analyst không khớp bằng chứng.");
      }
      return {
        role: word(r.role),
        stance: word(r.stance),
        rationale: word(r.rationale),
      };
    });
    if (new Set(parsed.map((item) => item.role)).size !== 5) {
      throw new Error("Vai trò analyst bị lặp.");
    }
    return {
      type: "panel", name: filename, model: word(root.model),
      verdict: word(advisory.verdict),
      rating: word(advisory.portfolio_research_rating),
      veto: advisory.risk_veto === true,
      reports: parsed, inputTokens: num(root.total_input_tokens),
      outputTokens: num(root.total_output_tokens),
      timestamp: typeof evidence.decision_ms === "number" ? evidence.decision_ms : null,
      evidence: digest(evidence.evidence_sha256),
    };
  }
  if (root.schema === schemaScore) {
    const score = record(root.score);
    if (!score ||
        score.schema !== schemaScore ||
        score.orders_submitted !== 0 ||
        score.net_profit_after_costs_computed !== false ||
        score.out_of_sample_profitability_proven !== false ||
        score.verdict !== "PASSED_FINITE_DIRECTION_COMPARISON_ONLY" ||
        !digest(score.score_sha256)) {
      throw new Error("Scorecard không đạt điều kiện dữ liệu nghiên cứu.");
    }
    if (!["BTCUSDT", "ETHUSDT", "BNBUSDT"].includes(word(score.symbol)) ||
        !["1m", "5m"].includes(word(score.interval))) {
      throw new Error("Symbol hoặc khung nến không được hỗ trợ.");
    }
    const result = score.advisory_direction_correct;
    const baselineResult = score.prior_closed_bar_direction_correct;
    if (![true, false, null].includes(result as null) ||
        ![true, false, null].includes(baselineResult as null)) {
      throw new Error("Trường đối chứng không hợp lệ.");
    }
    return {
      type: "scorecard", name: filename, symbol: word(score.symbol),
      interval: word(score.interval), direction: num(score.advisory_direction),
      baseline: num(score.prior_closed_bar_momentum_direction),
      result: result as boolean | null,
      baselineResult: baselineResult as boolean | null,
      rawReturn: word(score.raw_asset_return_decimal),
      rating: word(score.advisory_rating),
      sourceEnd: num(score.decision_candle_end_ms),
      outcomeEnd: num(score.first_forward_candle_end_ms),
      evidence: digest(score.score_sha256),
    };
  }
  throw new Error("Không nhận diện được schema báo cáo nghiên cứu ECONITH.");
}

function readAuditReceipt(
  value: unknown, report: ResearchReport, originalHash: string,
): AuditReceipt {
  const item = record(value);
  if (
    !item ||
    item.schema !== receiptSchema ||
    item.verdict !== receiptVerdict ||
    item.audit_mode !== receiptMode ||
    item.kind !== report.type ||
    item.report_sha256 !== originalHash ||
    item.evidence_sha256 !== report.evidence ||
    !digest(item.report_sha256) ||
    !digest(item.evidence_sha256) ||
    item.signed_by_trusted_authority !== false ||
    item.exchange_origin_verified !== false ||
    item.real_trading_results_proven !== false ||
    item.orders_submitted !== 0
  ) {
    throw new Error("Receipt không khớp báo cáo hoặc thiếu giới hạn an toàn.");
  }
  if (report.type === "scorecard" &&
      (item.symbol !== report.symbol || item.interval !== report.interval)) {
    throw new Error("Receipt thuộc một tài sản hoặc khung thời gian khác.");
  }
  return {
    kind: item.kind as AuditReceipt["kind"],
    report_sha256: item.report_sha256 as string,
    evidence_sha256: item.evidence_sha256 as string,
    verdict: item.verdict as string,
  };
}

async function sha256LocalFile(file: File): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("Trình duyệt không hỗ trợ WebCrypto SHA-256.");
  }
  const bytes = await file.arrayBuffer();
  const hash = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), (b) =>
    b.toString(16).padStart(2, "0")).join("");
}

function dateText(ms: number | null): string {
  return ms && ms > 0 ? new Date(ms).toLocaleString("vi-VN") : "—";
}
function direction(v: number): string {
  return v > 0 ? "Tăng" : v < 0 ? "Giảm" : "Không có tín hiệu";
}
function verdict(v: boolean | null): string {
  return v === true ? "Đúng hướng" : v === false ? "Sai hướng" : "Không đủ điều kiện chấm";
}

export default function ResearchPage() {
  const { locale } = useLocale();
  const en = locale === "en";
  const fileRef = useRef<HTMLInputElement | null>(null);
  const auditRef = useRef<HTMLInputElement | null>(null);
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [sourceHash, setSourceHash] = useState("");
  const [receipt, setReceipt] = useState<AuditReceipt | null>(null);
  const [auditError, setAuditError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function selectFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setBusy(true);
    setError("");
    setAuditError("");
    setReceipt(null);
    setSourceHash("");
    setReport(null);
    try {
      if (file.size > 512 * 1024 || file.size === 0) {
        throw new Error("Chỉ cho phép báo cáo JSON từ 1 byte đến 512 KB.");
      }
      const decoded: unknown = JSON.parse(await file.text());
      const parsed = readReport(decoded, file.name);
      const exactHash = await sha256LocalFile(file);
      setReport(parsed);
      setSourceHash(exactHash);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không thể đọc JSON.");
    } finally {
      setBusy(false);
    }
  }

  async function selectReceipt(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file || !report || !sourceHash) return;
    setReceipt(null);
    setAuditError("");
    try {
      if (file.size === 0 || file.size > 8 * 1024) {
        throw new Error("Receipt phải là JSON không rỗng, tối đa 8 KB.");
      }
      const raw: unknown = JSON.parse(await file.text());
      setReceipt(readAuditReceipt(raw, report, sourceHash));
    } catch (err) {
      setAuditError(err instanceof Error ? err.message : "Receipt không hợp lệ.");
    }
  }

  return (
    <div className="min-h-full bg-base text-ink">
      <div className="mx-auto w-full max-w-7xl px-4 pb-16 pt-10 sm:px-6">
        <div className="mb-8 flex flex-wrap items-start justify-between gap-5">
          <div className="max-w-3xl">
            <p className="mb-3 font-mono text-xs uppercase tracking-[.19em] text-accent">
              ECONITH / RESEARCH INTELLIGENCE
            </p>
            <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
              {en ? "Evidence & Research" : "Trung tâm bằng chứng nghiên cứu"}
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-7 text-muted">
              {en
                ? "Inspect a local, research-only analyst report or finite outcome scorecard. No trading connection, uploads or provider calls."
                : "Xem báo cáo hội đồng AI hoặc scorecard dự báo hữu hạn ngay trên thiết bị. Không kết nối sàn, không gửi file lên máy chủ, không gọi AI."}
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-line bg-elevated px-4 py-2 font-mono text-[11px] tracking-wide text-muted">
            <span className="h-2 w-2 rounded-full bg-amber-500" />
            RESEARCH ONLY · NO ORDERS
          </div>
        </div>

        <Link href="/research/overview"
          className="mb-5 inline-flex items-center rounded-xl border border-line bg-surface px-5 py-3 text-sm font-semibold text-accent transition-opacity hover:opacity-80">
          {en ? "Explore longitudinal AI vs baseline →" : "Xem hiệu quả dự báo AI so với baseline →"}
        </Link>

        <div className="mb-5 rounded-2xl border border-amber-500/30 bg-amber-500/5 px-5 py-4 text-sm leading-6 text-muted">
          <strong className="text-ink">
            {en ? "Evidence status: not independently verified in browser." : "Trạng thái: Chưa kiểm toán độc lập trong trình duyệt."}
          </strong>{" "}
          {en
            ? "This screen only checks a JSON format. A file can be edited or fabricated. Verify against original private SQLite/OHLCV files with the Python independent auditor before trusting any conclusion."
            : "Trang này chỉ kiểm tra cấu trúc JSON. File có thể bị sửa hoặc làm giả. Cần đối chiếu bằng công cụ kiểm toán Python với dữ liệu gốc SQLite/OHLCV riêng tư trước khi tin vào kết luận."}
        </div>

        <section className="mb-8 grid gap-4 lg:grid-cols-[1.3fr_.7fr]">
          <div className="rounded-2xl border border-line bg-surface p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-elevated text-accent">
                <span aria-hidden="true" className="font-mono text-xl">{"{ }"}</span>
              </span>
              <div>
                <h2 className="font-semibold">{en ? "Open local evidence" : "Mở báo cáo nghiên cứu trên máy"}</h2>
                <p className="text-xs text-muted">llm_advisory.json · finite_scorecard.json</p>
              </div>
            </div>
            <p className="mb-5 text-sm leading-6 text-muted">
              {en ? "Files are parsed locally in your browser and never uploaded. Raw datasets and API keys are not accepted."
                  : "File chỉ được đọc trong trình duyệt, không tải lên server. Không chọn file dữ liệu thô hoặc API key."}
            </p>
            <input
              ref={auditRef}
              className="sr-only"
              type="file"
              accept=".json,application/json"
              aria-label={en ? "Choose local offline audit receipt" : "Chọn receipt kiểm toán cục bộ"}
              onChange={selectReceipt}
            />
            <input
              ref={fileRef}
              className="sr-only"
              type="file"
              accept=".json,application/json"
              aria-label={en ? "Choose local JSON report" : "Chọn báo cáo JSON trên máy"}
              onChange={selectFile}
            />
            <div className="flex flex-wrap gap-3">
              <button type="button" onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="rounded-xl bg-accent px-5 py-2.5 text-sm font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-40">
                {busy ? "Đang đọc…" : en ? "Select JSON report" : "Chọn báo cáo JSON"}
              </button>
              {report && (
                <button type="button" onClick={() => auditRef.current?.click()}
                  disabled={busy}
                  className="rounded-xl border border-line bg-elevated px-5 py-2.5 text-sm font-semibold text-ink disabled:opacity-40">
                  {en ? "Match audit receipt" : "Đối chiếu receipt kiểm toán"}
                </button>
              )}
              {report && (
                <button type="button" onClick={() => { setReport(null); setError(""); setReceipt(null); setSourceHash(""); setAuditError(""); }}
                  className="rounded-xl border border-line bg-elevated px-5 py-2.5 text-sm text-ink">
                  {en ? "Clear" : "Xóa khỏi màn hình"}
                </button>
              )}
            </div>
            {error && <p role="alert" className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-500">{error}</p>}
            {auditError && <p role="alert" className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-500">{auditError}</p>}
            {receipt && (
              <div role="status" className="mt-4 rounded-xl border border-line bg-elevated p-4 text-xs leading-6 text-muted">
                <p className="font-semibold text-ink">
                  {en ? "Receipt matches this report's SHA-256 and evidence digest." : "Receipt khớp SHA-256 của báo cáo và mã băm bằng chứng."}
                </p>
                <p>
                  {en
                    ? "The receipt states that a separate Python process re-audited the private originals. This unsigned, user-supplied file does not prove that the audit ran or establish trusted provenance. Confirm the CLI result on a trusted machine."
                    : "Receipt tự khai báo đã được Python kiểm toán lại từ dữ liệu gốc. Vì chưa có chữ ký số, file do người dùng cung cấp không chứng minh cuộc kiểm toán đã diễn ra hoặc xác nhận nguồn tin cậy. Cần kiểm tra kết quả CLI trên máy đáng tin cậy."}
                </p>
              </div>
            )}
          </div>
          <div className="rounded-2xl border border-line bg-surface p-6">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-faint">
              {en ? "What this view does not prove" : "Những điều màn hình không chứng minh"}
            </p>
            <div className="mt-4 space-y-3 text-sm leading-6 text-muted">
              <p>01 · {en ? "No real-money trading or PnL" : "Không chứng nhận lợi nhuận tiền thật"}</p>
              <p>02 · {en ? "No original-source cryptographic audit" : "Chưa kiểm toán hash dữ liệu gốc"}</p>
              <p>03 · {en ? "No live exchange orders" : "Không có chức năng đặt lệnh sàn"}</p>
              <p>04 · {en ? "No 24–72 hour acceptance" : "Chưa nghiệm thu vận hành 24–72 giờ"}</p>
            </div>
          </div>
        </section>

        {report ? (
          <section aria-live="polite">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="font-mono text-[11px] uppercase tracking-widest text-faint">
                  {report.type === "panel" ? "LLM RESEARCH PANEL" : "FINITE FORWARD OUTCOME"}
                </p>
                <h2 className="mt-1 text-xl font-semibold">{report.name}</h2>
              </div>
              <span className="rounded-full border border-amber-500/30 px-3 py-1 text-xs text-amber-500">
                {receipt
                  ? en ? "Local audit receipt matched · Unsigned" : "Receipt cục bộ khớp · Chưa ký"
                  : en ? "Format checked · Source unaudited" : "Đã kiểm tra định dạng · Nguồn chưa xác minh"}
              </span>
            </div>

            {report.type === "panel" ? (
              <>
                <div className="mb-4 grid gap-3 sm:grid-cols-4">
                  {[
                    ["Model", report.model || "—"],
                    ["Portfolio", report.rating || "—"],
                    ["Risk gate", report.veto ? "VETO" : "NO VETO"],
                    ["Token I / O", String(report.inputTokens) + " / " + String(report.outputTokens)],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-xl border border-line bg-surface p-4">
                      <p className="font-mono text-[11px] uppercase text-faint">{label}</p>
                      <p className="mt-2 break-words text-lg font-semibold">{value}</p>
                    </div>
                  ))}
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {report.reports.map((agent) => (
                    <article key={agent.role} className="rounded-xl border border-line bg-surface p-5">
                      <div className="mb-4 flex items-center justify-between gap-2">
                        <h3 className="font-semibold capitalize">{agent.role}</h3>
                        <span className="rounded-lg bg-elevated px-2 py-1 font-mono text-xs">{agent.stance}</span>
                      </div>
                      <p className="break-words text-sm leading-6 text-muted">{agent.rationale}</p>
                    </article>
                  ))}
                </div>
                <p className="mt-5 text-xs text-muted">
                  {en ? "Decision deadline" : "Hạn chốt quyết định"}: {dateText(report.timestamp)} · {report.verdict}
                </p>
              </>
            ) : (
              <>
                <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                  {[
                    ["Market", report.symbol + " · " + report.interval],
                    [en ? "Advisory" : "Đánh giá AI", direction(report.direction)],
                    [en ? "AI direction" : "Kết quả AI", verdict(report.result)],
                    ["Baseline", verdict(report.baselineResult)],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-xl border border-line bg-surface p-5">
                      <p className="text-xs uppercase tracking-wide text-muted">{label}</p>
                      <p className="mt-2 text-lg font-semibold">{value}</p>
                    </div>
                  ))}
                </div>
                <div className="rounded-xl border border-line bg-surface p-5 text-sm text-muted">
                  <p className="mb-2 font-semibold text-ink">{en ? "Forward candle" : "Nến tiếp theo được quan sát"}</p>
                  <p>{dateText(report.sourceEnd)} → {dateText(report.outcomeEnd)}</p>
                  <p className="mt-2">{en ? "Raw price change" : "Biến động giá thô"}: {report.rawReturn}</p>
                  <p className="mt-2">{en ? "Rating" : "Đánh giá"}: {report.rating} · {en ? "Baseline direction" : "Hướng baseline"}: {direction(report.baseline)}</p>
                  <p className="mt-3 font-medium text-amber-500">{en ? "Not PnL, Sharpe, fills or a trade result." : "Không phải PnL, Sharpe, khớp lệnh hoặc hiệu suất giao dịch."}</p>
                </div>
              </>
            )}
            <div className="mt-4 rounded-xl border border-line bg-elevated px-5 py-4 font-mono text-[11px] text-muted">
              <p className="mb-1 uppercase tracking-widest">{en ? "Unverified file-declared SHA-256" : "SHA-256 do file tự khai báo — chưa xác minh"}</p>
              <p className="break-all">{report.evidence}</p>
            </div>
          </section>
        ) : (
          <div className="rounded-2xl border border-dashed border-line bg-elevated/20 px-6 py-12 text-center">
            <p className="text-sm font-medium text-muted">{en ? "No local report loaded" : "Chưa tải báo cáo nghiên cứu"}</p>
            <p className="mt-2 text-xs text-faint">{en ? "Select a finished ECONITH research JSON export above." : "Chọn bản xuất JSON từ nghiên cứu ECONITH ở phía trên."}</p>
          </div>
        )}
      </div>
    </div>
  );
}
