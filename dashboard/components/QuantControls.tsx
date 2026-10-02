"use client";

/**
 * ECONITH Quant :: Operator console with dual-mode gating.
 */
import { useEffect, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faBolt,
  faClock,
  faWaveSquare,
  faRotateRight,
  faPlay,
  faPause,
  faLock,
  faFlask,
  faShieldHalved,
} from "@fortawesome/free-solid-svg-icons";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";
import { Panel } from "@/components/quant/ui/Panel";
import {
  pauseTime,
  pauseRuntime,
  resetQuantSimulation,
  resumeRuntime,
  resumeTime,
  sentinelInject,
  sentinelReset,
  setQuantMode,
  type AnomalyKind,
  type QuantModeName,
} from "@/lib/api";

interface ScenarioReading {
  latency: number;
  risk: number;
  drawdown: number;
  price: number;
  alerts: number;
  safety: string;
}

interface ScenarioRun {
  kind: AnomalyKind;
  startedAt: number;
  baseline: ScenarioReading;
  latest: ScenarioReading;
  status: "running" | "done" | "failed";
  wasPaused: boolean;
  error?: string;
}

const SCENARIO_WINDOW_MS = 8000;

const COPY = {
  en: {
    title: "Operator console",
    modeLabel: "Operating mode",
    reality: "REALITY",
    simulation: "SIMULATION",
    realityDesc:
      "Sovereign live brain. World coupling blocked, anomaly injection disabled.",
    simulationDesc:
      "Sandbox RL. World ↔ Quant coupled, anomaly injection armed.",
    enterSim: "Enter simulation",
    exitSim: "Return to reality",
    switching: "Switching…",
    injectTitle: "Anomaly injection",
    locked:
      "Anomaly injection is locked in REALITY mode — the sovereign trading brain stays uncorrupted by synthetic shocks.",
    flashCrash: "Flash crash",
    latencySpike: "Latency shock",
    volSpike: "Volatility spike",
    rearm: "Re-arm Sentinel",
    running: "Running live test",
    done: "Observation completed",
    failed: "Test could not start",
    progress: "Live observation window",
    latency: "Latency",
    risk: "Estimated risk",
    drawdown: "Drawdown",
    price: "Market price",
    alerts: "Warnings",
    safety: "Safety state",
    actual: "Actual change reported by the system",
    simulationControls: "Simulation controls",
    pauseAll: "Pause World + Quant",
    playAll: "Run World + Quant",
    resetSimulation: "Reset simulation",
  },
  vi: {
    title: "Thử tình huống",
    modeLabel: "Cách Quant dùng dữ liệu",
    reality: "CHỈ DỮ LIỆU THỊ TRƯỜNG",
    simulation: "KẾT HỢP WORLD",
    realityDesc:
      "Quant chỉ dùng dữ liệu thị trường; World không tác động vào kết quả.",
    simulationDesc:
      "Quant kết hợp dữ liệu thị trường và tình huống từ World; không giao dịch thật.",
    enterSim: "Dùng World",
    exitSim: "Chỉ dùng thị trường",
    switching: "Đang chuyển…",
    injectTitle: "Tạo tình huống kiểm tra",
    locked:
      "Chỉ có thể tạo tình huống thử khi đang dùng World.",
    flashCrash: "Giảm giá nhanh",
    latencySpike: "Tăng độ trễ",
    volSpike: "Tăng biến động",
    rearm: "Bật lại kiểm soát rủi ro",
    running: "Đang chạy kiểm tra trực tiếp",
    done: "Đã hoàn tất theo dõi",
    failed: "Không thể bắt đầu kiểm tra",
    progress: "Khoảng theo dõi trực tiếp",
    latency: "Độ trễ",
    risk: "Rủi ro ước tính",
    drawdown: "Mức giảm",
    price: "Giá thị trường",
    alerts: "Cảnh báo",
    safety: "Trạng thái an toàn",
    actual: "Thay đổi thực tế hệ thống vừa ghi nhận",
    simulationControls: "Điều khiển mô phỏng",
    pauseAll: "Dừng World và Quant",
    playAll: "Chạy World và Quant",
    resetSimulation: "Đặt lại mô phỏng",
  },
} as const;

export function QuantControls() {
  const { snapshot } = useMetrics();
  const { locale } = useLocale();
  const c = COPY[locale === "vi" ? "vi" : "en"];

  const serverMode = snapshot?.quant_mode?.mode;
  const [mode, setMode] = useState<QuantModeName>(serverMode ?? "REALITY");
  const [switching, setSwitching] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [scenarioRun, setScenarioRun] = useState<ScenarioRun | null>(null);
  const [clock, setClock] = useState(0);
  const scenarioStatus = scenarioRun?.status;
  const scenarioStartedAt = scenarioRun?.startedAt;
  const scenarioWasPaused = scenarioRun?.wasPaused ?? false;

  useEffect(() => {
    if (serverMode && !switching) setMode(serverMode);
  }, [serverMode, switching]);

  const isReality = mode === "REALITY";
  const injectionEnabled = !isReality;
  const runtimeRunning = snapshot?.time?.running ?? false;

  const toggleMode = async () => {
    const next: QuantModeName = isReality ? "SIMULATION" : "REALITY";
    setSwitching(true);
    setMode(next);
    const res = await setQuantMode(next);
    if (res?.mode) setMode(res.mode);
    setSwitching(false);
  };

  const trigger = async (fn: () => Promise<unknown>, key: string) => {
    setBusy(key);
    await fn();
    setBusy(null);
  };

  const toggleRuntime = async () => {
    if (busy) return;
    setBusy("runtime");
    if (runtimeRunning) await pauseRuntime();
    else await resumeRuntime();
    setBusy(null);
  };

  const resetSimulation = async () => {
    if (busy) return;
    setBusy("simulation-reset");
    await resetQuantSimulation();
    setScenarioRun(null);
    setBusy(null);
  };

  const captureReading = (): ScenarioReading => ({
    latency: snapshot?.sentinel?.latency_ms ?? 0,
    risk: snapshot?.sentinel?.var ?? 0,
    drawdown: snapshot?.sentinel?.drawdown ?? 0,
    price: snapshot?.market?.price ?? 0,
    alerts: (snapshot?.events ?? []).filter(
      (event) => event.level === "warn" || event.level === "danger",
    ).length,
    safety: `${snapshot?.sentinel?.state ?? "—"} · ${snapshot?.sentinel?.mode ?? "—"}`,
  });

  const inject = async (kind: AnomalyKind) => {
    if (busy) return;
    const wasPaused = snapshot?.time?.running === false;
    const baseline = captureReading();
    setClock(Date.now());
    setBusy(kind);
    if (wasPaused) await resumeTime();
    const result = await sentinelInject(kind);
    if (!result?.injected) {
      setScenarioRun({
        kind,
        startedAt: Date.now(),
        baseline,
        latest: baseline,
        status: "failed",
        wasPaused,
        error: result?.error ?? "request failed",
      });
      if (wasPaused) await pauseTime();
    } else {
      setScenarioRun({
        kind,
        startedAt: Date.now(),
        baseline,
        latest: baseline,
        status: "running",
        wasPaused,
      });
    }
    setBusy(null);
  };

  useEffect(() => {
    if (!scenarioRun || scenarioRun.status !== "running") return;
    const latest = captureReading();
    setScenarioRun((current) => current && current.status === "running"
      ? { ...current, latest }
      : current);
  // Snapshot identity changes only when the backend publishes a new reading.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot]);

  useEffect(() => {
    if (scenarioStatus !== "running" || scenarioStartedAt == null) return;
    const timer = window.setInterval(() => {
      const now = Date.now();
      setClock(now);
      if (now - scenarioStartedAt < SCENARIO_WINDOW_MS) return;
      setScenarioRun((current) => current && current.status === "running"
        ? { ...current, status: "done" }
        : current);
      if (scenarioWasPaused) void pauseTime();
    }, 200);
    return () => window.clearInterval(timer);
  }, [scenarioStartedAt, scenarioStatus, scenarioWasPaused]);

  return (
    <Panel
      title={c.title}
      icon={faShieldHalved}
      zone="risk"
      bodyClassName="flex flex-col gap-3"
    >
      <div className="rounded-lg border border-line bg-elevated p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-faint">
              {c.modeLabel}
            </p>
            <p
              className={`font-mono text-sm font-bold ${
                isReality ? "text-ok" : "text-warn"
              }`}
            >
              {isReality ? c.reality : c.simulation}
            </p>
          </div>
          <button
            type="button"
            onClick={toggleMode}
            disabled={switching}
            className={[
              "shrink-0 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors disabled:opacity-50",
              isReality
                ? "border-warn/50 bg-warn/10 text-warn hover:bg-warn hover:text-black"
                : "border-ok/50 bg-ok/10 text-ok hover:bg-ok hover:text-black",
            ].join(" ")}
          >
            <FontAwesomeIcon
              icon={isReality ? faFlask : faShieldHalved}
              className="mr-1.5 h-3 w-3"
            />
            {switching ? c.switching : isReality ? c.enterSim : c.exitSim}
          </button>
        </div>
        <p className="mt-2 text-[11px] leading-snug text-muted">
          {isReality ? c.realityDesc : c.simulationDesc}
        </p>
      </div>

      <div className="rounded-lg border border-line bg-base p-3">
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
          {c.simulationControls}
        </p>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void toggleRuntime()}
            disabled={busy !== null}
            className={`quant-ctrl-btn ${
              runtimeRunning
                ? "border-danger bg-danger/10 text-danger hover:bg-danger hover:text-white"
                : "border-ok bg-ok/10 text-ok hover:bg-ok hover:text-black"
            }`}
          >
            <FontAwesomeIcon icon={runtimeRunning ? faPause : faPlay} className="h-3 w-3" />
            {runtimeRunning ? c.pauseAll : c.playAll}
          </button>
          <button
            type="button"
            onClick={() => void resetSimulation()}
            disabled={busy !== null}
            className="quant-ctrl-btn border-line bg-elevated text-ink hover:bg-base"
          >
            <FontAwesomeIcon icon={faRotateRight} className="h-3 w-3" />
            {c.resetSimulation}
          </button>
        </div>
      </div>

      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
          {c.injectTitle}
        </p>

        {injectionEnabled ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void inject("shock")}
              disabled={busy === "shock"}
              className="quant-ctrl-btn border-danger bg-danger/10 text-danger hover:bg-danger hover:text-white"
            >
              <FontAwesomeIcon icon={faBolt} className="h-3 w-3" />
              {c.flashCrash}
            </button>
            <button
              type="button"
              onClick={() => void inject("latency")}
              disabled={busy === "latency"}
              className="quant-ctrl-btn border-warn bg-warn/10 text-warn hover:bg-warn hover:text-black"
            >
              <FontAwesomeIcon icon={faClock} className="h-3 w-3" />
              {c.latencySpike}
            </button>
            <button
              type="button"
              onClick={() => void inject("vol")}
              disabled={busy === "vol"}
              className="quant-ctrl-btn border-warn bg-warn/10 text-warn hover:bg-warn hover:text-black"
            >
              <FontAwesomeIcon icon={faWaveSquare} className="h-3 w-3" />
              {c.volSpike}
            </button>
          </div>
        ) : (
          <div className="flex items-start gap-2 rounded-lg border border-line bg-base px-3 py-2">
            <FontAwesomeIcon
              icon={faLock}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 text-faint"
            />
            <p className="text-[11px] leading-snug text-muted">{c.locked}</p>
          </div>
        )}
      </div>

      {scenarioRun ? (
        <ScenarioObservation
          run={scenarioRun}
          now={clock}
          labels={c}
        />
      ) : null}

      <div className="border-t border-line pt-3">
        <button
          type="button"
          onClick={() => trigger(sentinelReset, "reset")}
          disabled={busy === "reset"}
          className="quant-ctrl-btn w-full border-line bg-elevated text-ink hover:bg-base"
        >
          <FontAwesomeIcon icon={faRotateRight} className="h-3 w-3" />
          {c.rearm}
        </button>
      </div>
    </Panel>
  );
}

function ScenarioObservation({
  run,
  now,
  labels,
}: {
  run: ScenarioRun;
  now: number;
  labels: typeof COPY.vi | typeof COPY.en;
}) {
  const progress = run.status === "done"
    ? 100
    : Math.min(100, ((now - run.startedAt) / SCENARIO_WINDOW_MS) * 100);
  const title = run.kind === "shock"
    ? labels.flashCrash
    : run.kind === "latency"
      ? labels.latencySpike
      : labels.volSpike;
  const metricRows = [
    {
      label: labels.latency,
      before: `${run.baseline.latency.toFixed(0)} ms`,
      after: `${run.latest.latency.toFixed(0)} ms`,
      changed: Math.abs(run.latest.latency - run.baseline.latency) > 1,
    },
    {
      label: labels.risk,
      before: `${(run.baseline.risk * 100).toFixed(2)}%`,
      after: `${(run.latest.risk * 100).toFixed(2)}%`,
      changed: Math.abs(run.latest.risk - run.baseline.risk) > 1e-5,
    },
    {
      label: labels.drawdown,
      before: `${(run.baseline.drawdown * 100).toFixed(2)}%`,
      after: `${(run.latest.drawdown * 100).toFixed(2)}%`,
      changed: Math.abs(run.latest.drawdown - run.baseline.drawdown) > 1e-5,
    },
    {
      label: labels.price,
      before: `$${run.baseline.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
      after: `$${run.latest.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}`,
      changed: Math.abs(run.latest.price - run.baseline.price) > 0.01,
    },
  ];
  return (
    <section className="overflow-hidden rounded-xl border border-world/35 bg-world/5">
      <div className="flex items-center justify-between gap-3 border-b border-line px-3 py-2.5">
        <div>
          <p className="text-[9px] font-semibold uppercase tracking-[0.16em] text-world">{labels.actual}</p>
          <p className="mt-0.5 text-xs font-bold text-ink">{title}</p>
        </div>
        <span className={`rounded-full px-2 py-1 text-[9px] font-semibold ${run.status === "failed" ? "bg-danger/10 text-danger" : run.status === "done" ? "bg-ok/10 text-ok" : "bg-world/15 text-world"}`}>
          {run.status === "failed" ? labels.failed : run.status === "done" ? labels.done : labels.running}
        </span>
      </div>
      {run.status === "failed" ? (
        <p className="px-3 py-3 text-[11px] text-danger">{run.error}</p>
      ) : (
        <>
          <div className="px-3 pt-3">
            <div className="mb-1 flex justify-between text-[9px] text-faint"><span>{labels.progress}</span><span>{Math.round(progress)}%</span></div>
            <div className="h-1.5 overflow-hidden rounded-full bg-base"><div className="h-full rounded-full bg-world transition-[width] duration-200" style={{ width: `${progress}%` }} /></div>
          </div>
          <div className="grid grid-cols-1 gap-1.5 p-3 sm:grid-cols-2">
            {metricRows.map((row) => (
              <div key={row.label} className="rounded-lg border border-line bg-surface px-2.5 py-2">
                <p className="text-[9px] uppercase tracking-wide text-faint">{row.label}</p>
                <p className="mt-1 flex items-center gap-1.5 font-mono text-[11px]"><span className="text-muted">{row.before}</span><span className="text-faint">→</span><strong className={row.changed ? "text-world" : "text-muted"}>{row.after}</strong></p>
              </div>
            ))}
          </div>
          <div className="flex items-center justify-between border-t border-line px-3 py-2 text-[10px]"><span className="text-muted">{labels.safety}</span><strong className="font-mono text-ink">{run.baseline.safety} → {run.latest.safety}</strong></div>
        </>
      )}
    </section>
  );
}
