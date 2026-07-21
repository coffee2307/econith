"use client";

/**
 * ECONITH :: Main System Control Dashboard (Task 1).
 *
 * The operator control plane over `core/system_controller.py`:
 *   - Operating-mode state machine (5 regimes)
 *   - Compute Optimization Guardrail (Enable World Simulation master switch)
 *   - World -> Quant bridge toggle
 *
 * All actions optimistically update local state, then reconcile with the
 * authoritative snapshot the backend returns. NOTE: Tailwind is JIT-purged, so
 * every class string here is static (no runtime interpolation of tokens).
 */
import { useCallback, useEffect, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import {
  faSatelliteDish,
  faFlask,
  faWandMagicSparkles,
  faSliders,
  faRobot,
  faMicrochip,
  faLink,
  faBolt,
} from "@fortawesome/free-solid-svg-icons";
import { useLocale } from "@/contexts/LocaleContext";
import {
  getControlState,
  getLastApiError,
  setOperatingMode,
  setWorldSimulation,
  setWorldBridge,
  type OperatingModeName,
  type SystemControlState,
} from "@/lib/api";

type Accent = "ok" | "warn" | "danger" | "faint";

const MODE_ICON: Record<OperatingModeName, IconDefinition> = {
  REALITY: faSatelliteDish,
  SIMULATION: faFlask,
  AUTONOMOUS_HYPOTHESIS: faWandMagicSparkles,
  USER_HYPOTHESIS: faSliders,
  FULLY_AUTONOMOUS: faRobot,
};

// Static class strings per accent (Tailwind must see the full literal).
const MODE_ACTIVE_CLASS: Record<OperatingModeName, string> = {
  REALITY: "border-ok/60 bg-ok/10 text-ok",
  SIMULATION: "border-warn/60 bg-warn/10 text-warn",
  AUTONOMOUS_HYPOTHESIS: "border-warn/60 bg-warn/10 text-warn",
  USER_HYPOTHESIS: "border-warn/60 bg-warn/10 text-warn",
  FULLY_AUTONOMOUS: "border-danger/60 bg-danger/10 text-danger",
};

const CHIP_TONE_CLASS: Record<Accent, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  faint: "text-faint",
};

const MODE_ORDER: OperatingModeName[] = [
  "REALITY",
  "SIMULATION",
  "AUTONOMOUS_HYPOTHESIS",
  "USER_HYPOTHESIS",
  "FULLY_AUTONOMOUS",
];

const COPY = {
  en: {
    title: "Main System Control",
    subtitle: "Operating regime · compute guardrail · World↔Quant bridge",
    modes: "Execution modes",
    m_REALITY: "Reality",
    m_SIMULATION: "Simulation",
    m_AUTONOMOUS_HYPOTHESIS: "Autonomous Hypothesis",
    m_USER_HYPOTHESIS: "User Hypothesis",
    m_FULLY_AUTONOMOUS: "Fully Autonomous Loop",
    d_REALITY: "Live market data + real-time execution.",
    d_SIMULATION: "Paper trading in historical / synthetic environments.",
    d_AUTONOMOUS_HYPOTHESIS: "AI self-generates macro shock hypotheses and tests them.",
    d_USER_HYPOTHESIS: "Manually tweak macro variables to test custom scenarios.",
    d_FULLY_AUTONOMOUS:
      "Not implemented yet — flag only. No automatic retrain→deploy loop.",
    compute: "Compute optimization guardrail",
    worldSim: "Enable World Simulation",
    worldSimOn: "Agent pipeline ACTIVE — full simulation compute.",
    worldSimOff: "Agent pipeline SUSPENDED — market data only (CPU/GPU/RAM freed).",
    bridge: "World → Quant bridge",
    bridgeOn: "Simulation state matrices feed the Quant reward/state.",
    bridgeOff: "Quant brain isolated from World coupling.",
    quantMode: "Sovereign gate",
    computeProfile: "Compute",
    coupling: "Coupling",
    active: "ACTIVE",
    suspended: "SUSPENDED",
    on: "ON",
    off: "OFF",
    full: "FULL",
    marketOnly: "MARKET-ONLY",
    desks: "Desks",
    regimeBrain: "Regime",
    macroProv: "Macro",
    hypothesis: "Hypothesis",
    trained: "trained",
    heuristic: "heuristic",
    live: "live",
    mock: "mock",
  },
  vi: {
    title: "Điều khiển hệ thống",
    subtitle: "Chế độ vận hành · guardrail điện toán · cầu World↔Quant",
    modes: "Chế độ thực thi",
    m_REALITY: "Thực tế",
    m_SIMULATION: "Mô phỏng",
    m_AUTONOMOUS_HYPOTHESIS: "Giả thuyết tự động",
    m_USER_HYPOTHESIS: "Giả thuyết người dùng",
    m_FULLY_AUTONOMOUS: "Vòng lặp tự trị",
    d_REALITY: "Dữ liệu thị trường trực tiếp + thực thi thời gian thực.",
    d_SIMULATION: "Giao dịch giấy trong môi trường lịch sử / tổng hợp.",
    d_AUTONOMOUS_HYPOTHESIS: "AI tự sinh giả thuyết cú sốc vĩ mô và kiểm thử.",
    d_USER_HYPOTHESIS: "Tự chỉnh biến vĩ mô để thử kịch bản tùy biến.",
    d_FULLY_AUTONOMOUS:
      "Chưa triển khai — chỉ là cờ trạng thái, chưa có vòng train→deploy.",
    compute: "Guardrail tối ưu điện toán",
    worldSim: "Bật mô phỏng World",
    worldSimOn: "Pipeline tác tử ĐANG CHẠY — dùng full điện toán mô phỏng.",
    worldSimOff: "Pipeline tác tử TẠM DỪNG — chỉ dữ liệu thị trường (giải phóng CPU/GPU/RAM).",
    bridge: "Cầu World → Quant",
    bridgeOn: "Ma trận trạng thái mô phỏng nạp vào reward/state của Quant.",
    bridgeOff: "Bộ não Quant cô lập khỏi ghép nối World.",
    quantMode: "Cổng chủ quyền",
    computeProfile: "Điện toán",
    coupling: "Ghép nối",
    active: "ĐANG BẬT",
    suspended: "TẠM DỪNG",
    on: "BẬT",
    off: "TẮT",
    full: "ĐẦY ĐỦ",
    marketOnly: "CHỈ THỊ TRƯỜNG",
    desks: "Desks",
    regimeBrain: "Chế độ",
    macroProv: "Vĩ mô",
    hypothesis: "Giả thuyết",
    trained: "đã train",
    heuristic: "heuristic",
    live: "live",
    mock: "mock",
  },
} as const;

export function MainControlDashboard() {
  const { locale } = useLocale();
  const c = COPY[locale === "vi" ? "vi" : "en"];
  const [state, setState] = useState<SystemControlState | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const s = await getControlState();
    if (s) setState(s);
  }, []);

  useEffect(() => {
    void refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const run = useCallback(
    async (key: string, fn: () => Promise<SystemControlState | null>) => {
      setBusy(key);
      setApiError(null);
      const next = await fn();
      if (next) setState(next);
      else {
        const err = getLastApiError();
        setApiError(
          err ? `${err.status || "net"}: ${err.detail}` : "Request failed",
        );
        await refresh();
      }
      setBusy(null);
    },
    [refresh],
  );

  const activeMode = state?.operating_mode ?? "REALITY";
  const worldOn = state?.world_simulation_enabled ?? false;
  const bridgeOn = state?.world_to_quant_bridge ?? true;
  const brainLabel = (raw?: string) => {
    if (!raw) return "—";
    if (raw === "trained" || raw.startsWith("trained")) return c.trained;
    if (raw === "heuristic") return c.heuristic;
    return raw;
  };
  const macroChip = (() => {
    const prov = state?.macro_provenance;
    if (!prov || Object.keys(prov).length === 0) return "—";
    const values = Object.values(prov).map((v) => v?.provenance || "unknown");
    if (values.every((v) => v === "live")) return c.live;
    if (values.every((v) => v === "mock")) return c.mock;
    return "mixed";
  })();
  const hypStatus = (() => {
    if (!state?.autonomous_hypothesis) return "—";
    const h = state.hypothesis;
    if (!h) return state.autonomous_hypothesis_implemented ? "armed?" : "n/a";
    if (h.last_status) return `${h.last_status}${h.last_id ? ` · ${h.last_id}` : ""}`;
    return h.armed ? "armed" : "idle";
  })();

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <header className="mb-4">
        <h2 className="flex items-center gap-2 text-sm font-bold uppercase tracking-wider text-ink">
          <FontAwesomeIcon icon={faBolt} className="h-3.5 w-3.5 text-accent" />
          {c.title}
        </h2>
        <p className="mt-0.5 text-[11px] text-muted">{c.subtitle}</p>
      </header>

      {/* ---- Execution mode state machine ---- */}
      <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
        {c.modes}
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {MODE_ORDER.map((m) => {
          const active = activeMode === m;
          const unimplemented =
            m === "FULLY_AUTONOMOUS" &&
            state?.autonomous_loop_implemented === false;
          return (
            <button
              key={m}
              type="button"
              disabled={busy !== null || unimplemented}
              title={unimplemented ? c[`d_${m}` as const] : undefined}
              onClick={() => {
                if (unimplemented) return;
                void run(`mode-${m}`, () => setOperatingMode(m));
              }}
              className={[
                "flex flex-col items-start rounded-lg border p-3 text-left transition-colors disabled:opacity-50",
                active
                  ? MODE_ACTIVE_CLASS[m]
                  : "border-line bg-elevated text-ink hover:bg-base",
                unimplemented ? "cursor-not-allowed opacity-60" : "",
              ].join(" ")}
            >
              <span className="flex items-center gap-2 text-xs font-bold">
                <FontAwesomeIcon icon={MODE_ICON[m]} className="h-3.5 w-3.5" />
                {c[`m_${m}` as const]}
              </span>
              <span className="mt-1 text-[11px] leading-snug text-muted">
                {c[`d_${m}` as const]}
              </span>
            </button>
          );
        })}
      </div>
      {apiError ? (
        <p className="mt-2 text-[11px] text-danger" role="alert">
          {apiError}
        </p>
      ) : null}

      {/* ---- Compute guardrail + bridge toggles ---- */}
      <p className="mb-2 mt-4 text-[11px] font-semibold uppercase tracking-wider text-faint">
        {c.compute}
      </p>
      <div className="grid grid-cols-1 gap-2 lg:grid-cols-2">
        <ToggleRow
          icon={faMicrochip}
          label={c.worldSim}
          desc={worldOn ? c.worldSimOn : c.worldSimOff}
          on={worldOn}
          danger={!worldOn}
          busy={busy === "world-sim"}
          onToggle={() => run("world-sim", () => setWorldSimulation(!worldOn))}
        />
        <ToggleRow
          icon={faLink}
          label={c.bridge}
          desc={bridgeOn ? c.bridgeOn : c.bridgeOff}
          on={bridgeOn}
          busy={busy === "world-bridge"}
          onToggle={() => run("world-bridge", () => setWorldBridge(!bridgeOn))}
        />
      </div>

      {/* ---- Live status strip ---- */}
      <div className="mt-4 grid grid-cols-2 gap-2 text-center sm:grid-cols-3 lg:grid-cols-6">
        <StatChip
          label={c.quantMode}
          value={state?.quant_mode ?? "—"}
          tone={state?.quant_mode === "REALITY" ? "ok" : "warn"}
        />
        <StatChip
          label={c.computeProfile}
          value={state?.compute_profile === "FULL" ? c.full : c.marketOnly}
          tone={state?.compute_profile === "FULL" ? "ok" : "faint"}
        />
        <StatChip
          label={c.coupling}
          value={state?.coupling_effective ? c.on : c.off}
          tone={state?.coupling_effective ? "warn" : "faint"}
        />
        <StatChip
          label={c.desks}
          value={brainLabel(state?.agent_brain)}
          tone={
            state?.agent_brain === "heuristic"
              ? "warn"
              : state?.agent_brain
                ? "ok"
                : "faint"
          }
        />
        <StatChip
          label={c.macroProv}
          value={macroChip}
          tone={
            macroChip === c.live ? "ok" : macroChip === c.mock ? "warn" : "faint"
          }
        />
        <StatChip
          label={c.hypothesis}
          value={hypStatus}
          tone={
            state?.hypothesis?.last_status === "ok"
              ? "ok"
              : state?.hypothesis?.last_status === "error"
                ? "danger"
                : "faint"
          }
        />
      </div>
      {state?.hypothesis?.last_prompt && state.autonomous_hypothesis ? (
        <p className="mt-2 truncate font-mono text-[10px] text-muted" title={state.hypothesis.last_prompt}>
          {state.hypothesis.last_prompt}
        </p>
      ) : null}
    </section>
  );
}

// Static toggle-track classes (full literals for Tailwind JIT).
const TRACK_ON: Record<"ok" | "danger", string> = {
  ok: "border-ok/60 bg-ok/30",
  danger: "border-danger/60 bg-danger/30",
};
const KNOB_ON: Record<"ok" | "danger", string> = {
  ok: "left-[22px] bg-ok",
  danger: "left-[22px] bg-danger",
};

function ToggleRow(props: {
  icon: IconDefinition;
  label: string;
  desc: string;
  on: boolean;
  danger?: boolean;
  busy: boolean;
  onToggle: () => void;
}) {
  const { icon, label, desc, on, danger, busy, onToggle } = props;
  const tone: "ok" | "danger" = danger ? "danger" : "ok";
  return (
    <div className="flex items-start justify-between gap-3 rounded-lg border border-line bg-elevated p-3">
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-xs font-semibold text-ink">
          <FontAwesomeIcon icon={icon} className="h-3.5 w-3.5 text-faint" />
          {label}
        </p>
        <p className="mt-1 text-[11px] leading-snug text-muted">{desc}</p>
      </div>
      <button
        type="button"
        disabled={busy}
        onClick={onToggle}
        aria-pressed={on}
        className={[
          "relative h-6 w-11 shrink-0 rounded-full border transition-colors disabled:opacity-50",
          on ? TRACK_ON[tone] : "border-line bg-base",
        ].join(" ")}
      >
        <span
          className={[
            "absolute top-0.5 h-4 w-4 rounded-full transition-all",
            on ? KNOB_ON[tone] : "left-0.5 bg-faint",
          ].join(" ")}
        />
      </button>
    </div>
  );
}

function StatChip(props: { label: string; value: string; tone: Accent }) {
  const { label, value, tone } = props;
  return (
    <div className="rounded-lg border border-line bg-elevated px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-wider text-faint">{label}</p>
      <p className={`font-mono text-xs font-bold ${CHIP_TONE_CLASS[tone]}`}>
        {value}
      </p>
    </div>
  );
}
