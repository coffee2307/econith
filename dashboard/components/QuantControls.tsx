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
  faLock,
  faFlask,
  faShieldHalved,
} from "@fortawesome/free-solid-svg-icons";
import { useMetrics } from "@/components/MetricsProvider";
import { useLocale } from "@/contexts/LocaleContext";
import { Panel } from "@/components/quant/ui/Panel";
import {
  sentinelInject,
  sentinelReset,
  setQuantMode,
  type AnomalyKind,
  type QuantModeName,
} from "@/lib/api";

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

  useEffect(() => {
    if (serverMode && !switching) setMode(serverMode);
  }, [serverMode, switching]);

  const isReality = mode === "REALITY";
  const injectionEnabled = !isReality;

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
  const inject = (kind: AnomalyKind) =>
    trigger(() => sentinelInject(kind), kind);

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

      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-faint">
          {c.injectTitle}
        </p>

        {injectionEnabled ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => inject("shock")}
              disabled={busy === "shock"}
              className="quant-ctrl-btn border-danger bg-danger/10 text-danger hover:bg-danger hover:text-white"
            >
              <FontAwesomeIcon icon={faBolt} className="h-3 w-3" />
              {c.flashCrash}
            </button>
            <button
              type="button"
              onClick={() => inject("latency")}
              disabled={busy === "latency"}
              className="quant-ctrl-btn border-warn bg-warn/10 text-warn hover:bg-warn hover:text-black"
            >
              <FontAwesomeIcon icon={faClock} className="h-3 w-3" />
              {c.latencySpike}
            </button>
            <button
              type="button"
              onClick={() => inject("vol")}
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
