"use client";

/**
 * ECONITH Quant — focused trading control deck.
 * Live market + AI decision + Sentinel risk + operator actions + event log.
 */
import { useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faShieldHalved,
  faBrain,
  faLock,
  faCircleDot,
  faSatelliteDish,
} from "@fortawesome/free-solid-svg-icons";
import { useMetrics } from "@/components/MetricsProvider";
import { EventLogTerminal } from "@/components/EventLogTerminal";
import { QuantControls } from "@/components/QuantControls";
import { QuantCockpitHUD } from "@/components/quant/cockpit/QuantCockpitHUD";
import { useLocale } from "@/contexts/LocaleContext";
import { fmtNum, fmtPct, fmtSigned, fmtUsd } from "@/lib/format";
import type { ConnectionStatus } from "@/hooks/useMetricsStream";
import { useExecutionStatus, type ExecutionRouting } from "@/hooks/useExecutionStatus";

const BREAKER_COLOR: Record<string, string> = {
  CLOSED: "text-ok",
  HALF_OPEN: "text-warn",
  OPEN: "text-danger",
};
const MODE_COLOR: Record<string, string> = {
  NORMAL: "text-ok",
  REDUCE_ONLY: "text-warn",
  FROZEN: "text-danger",
};
const CONN_COLOR: Record<ConnectionStatus, string> = {
  open: "text-ok",
  connecting: "text-warn",
  reconnecting: "text-warn",
  closed: "text-danger",
};

const EXEC_STYLE: Record<ExecutionRouting, { color: string; bg: string; border: string; pulse: boolean }> = {
  LIVE: { color: "text-emerald-400", bg: "bg-emerald-500/10", border: "border-emerald-500/40", pulse: true },
  SYNTHETIC: { color: "text-sky-400", bg: "bg-sky-500/10", border: "border-sky-500/40", pulse: false },
  DEGRADED: { color: "text-amber-400", bg: "bg-amber-500/10", border: "border-amber-500/40", pulse: true },
  OFFLINE: { color: "text-red-400", bg: "bg-red-500/10", border: "border-red-500/40", pulse: true },
};

export function QuantMissionControl() {
  const { snapshot, status, attempts } = useMetrics();
  const { t } = useLocale();
  const { execution } = useExecutionStatus();

  const market = snapshot?.market;
  const sentinel = snapshot?.sentinel;
  const ai = snapshot?.ai;
  const events = snapshot?.events ?? [];
  const quantMode = snapshot?.quant_mode?.mode ?? "REALITY";

  const breaker = sentinel?.state ?? "—";
  const mode = sentinel?.mode ?? "—";

  const spread =
    market?.best_bid != null && market?.best_ask != null
      ? market.best_ask - market.best_bid
      : null;

  const connLabel =
    status === "open"
      ? t("connection.live")
      : status === "closed"
        ? t("connection.offline")
        : status === "reconnecting"
          ? t("connection.reconnecting")
          : t("connection.connecting");

  return (
    <div className="quant-page-shell h-full min-h-0 w-full max-w-[100vw] flex-1 overflow-hidden px-4 py-3 lg:px-6 lg:py-4">
      {/* Status bar */}
      <div className="flex flex-none flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-lg font-bold tracking-tight text-ink sm:text-xl">
              {t("quant.eyebrow")}
            </h1>
            <span className="inline-flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-amber-700 dark:text-amber-300">
              <FontAwesomeIcon icon={faLock} className="h-2.5 w-2.5" />
              {t("quant.mission.testBanner")}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">{t("quant.mission.testBannerDesc")}</p>
        </div>
        <div className="flex flex-wrap gap-2 font-mono text-[11px]">
          <Badge label={t("quant.mission.wsLabel")} value={connLabel} className={CONN_COLOR[status]} pulse={status === "open"} extra={status === "reconnecting" && attempts ? `#${attempts}` : undefined} />
          <Badge label={t("quant.mission.symbolLabel")} value={market?.symbol ?? "—"} />
          <Badge label={t("quant.breaker")} value={breaker} className={BREAKER_COLOR[breaker] ?? "text-muted"} />
          <Badge label={t("quant.mode")} value={mode} className={MODE_COLOR[mode] ?? "text-muted"} />
          <Badge
            label="QUANT"
            value={quantMode}
            className={quantMode === "REALITY" ? "text-ok" : "text-warn"}
            pulse={quantMode === "SIMULATION"}
          />
          {execution ? (
            <ExecutionBadge
              routing={execution.execution_routing}
              detail={execution.detail}
              testnet={execution.testnet}
            />
          ) : null}
        </div>
      </div>

      {/* Market strip */}
      <section className="panel flex-none p-4">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <Cell label={`${t("quant.price")} ${market?.symbol ?? ""}`} value={fmtUsd(market?.price)} large />
          <Cell label={t("quant.mid")} value={fmtUsd(market?.mid)} />
          <Cell label={t("quant.telemetry.spread")} value={spread != null ? fmtUsd(spread, 4) : "—"} />
          <Cell
            label={t("quant.obi")}
            value={fmtNum(market?.obi)}
            accent={(market?.obi ?? 0) > 0 ? "text-ok" : (market?.obi ?? 0) < 0 ? "text-danger" : undefined}
          />
          <Cell
            label={t("quant.volumeDelta")}
            value={fmtSigned(market?.volume_delta)}
            accent={(market?.volume_delta ?? 0) > 0 ? "text-ok" : (market?.volume_delta ?? 0) < 0 ? "text-danger" : undefined}
          />
          <Cell label={t("quant.telemetry.trades")} value={market?.trade_count?.toLocaleString() ?? "—"} />
        </div>
      </section>

      {/* Wide desktop body: analytics flank (left) + persistent log flank (right) */}
      <div className="quant-body-grid grid min-h-0 grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_22rem] lg:gap-4 xl:grid-cols-[minmax(0,1fr)_26rem]">
        {/* Left flank — core analytics stack, independent scroll */}
        <div className="quant-analytics-flank flex min-h-0 flex-col gap-3 overflow-y-auto overflow-x-hidden pr-0 lg:gap-4 lg:pr-1">
          {/* Aviation cockpit HUD — PnL, margin, fills, allocation */}
          <div className="quant-cockpit-deck min-h-0 flex-none">
            <QuantCockpitHUD />
          </div>

          {/* AI + Sentinel/controls */}
          <div className="grid min-h-0 flex-none grid-cols-1 gap-3 xl:grid-cols-2 xl:gap-4">
            {/* AI */}
            <section className="panel flex min-h-0 flex-col p-3 sm:p-4">
              <header className="mb-2 flex shrink-0 items-center justify-between gap-2">
                <h2 className="flex items-center gap-2 text-sm font-semibold">
                  <FontAwesomeIcon icon={faBrain} className="h-4 w-4 text-accent" />
                  {t("quant.aiDecision")}
                </h2>
                <span className="shrink-0 font-mono text-[10px] text-muted">
                  {t("quant.regime")} {ai?.regime ?? "—"} ({fmtPct(ai?.regime_confidence ?? 0, 0)})
                </span>
              </header>
              <div className="grid min-h-0 flex-1 gap-3 text-[11px] sm:grid-cols-[7.5rem_1fr_1fr] sm:items-stretch">
                <div className="flex flex-col justify-center rounded-lg border border-line bg-elevated px-2 py-3 text-center">
                  <p className="text-[10px] uppercase tracking-wider text-faint">{t("quant.action")}</p>
                  <p
                    className={`text-3xl font-bold leading-none ${
                      ai?.action === "LONG" ? "text-ok" : ai?.action === "SHORT" ? "text-danger" : "text-muted"
                    }`}
                  >
                    {ai?.action ?? "—"}
                  </p>
                  <p className="mt-1.5 font-mono text-[10px] leading-snug text-muted">
                    {fmtSigned(ai?.direction, 3)} · {fmtPct(ai?.confidence ?? 0, 0)}
                  </p>
                </div>
                <MiniTable title={t("quant.agentAllocation")} fill>
                  {ai?.weights
                    ? Object.entries(ai.weights).map(([k, w]) => (
                        <Row key={k} left={k} right={fmtPct(w, 0)} />
                      ))
                    : "—"}
                </MiniTable>
                <MiniTable title={t("quant.featureAttribution")} fill>
                  {ai?.explain?.attribution
                    ? ai.explain.attribution.slice(0, 4).map((a) => (
                        <Row
                          key={a.feature}
                          left={a.feature}
                          right={fmtSigned(a.importance, 3)}
                          rightClass={a.importance >= 0 ? "text-ok" : "text-danger"}
                        />
                      ))
                    : "—"}
                </MiniTable>
              </div>
            </section>

            {/* Sentinel telemetry + mode-gated operator console */}
            <div className="flex min-h-0 flex-col gap-3 lg:gap-4">
              <section className="panel shrink-0 p-3 sm:p-4">
                <header className="mb-2 flex items-center gap-2">
                  <FontAwesomeIcon icon={faShieldHalved} className={`h-4 w-4 ${BREAKER_COLOR[breaker] ?? "text-muted"}`} />
                  <h2 className="text-sm font-semibold">{t("quant.sentinelLayer")}</h2>
                </header>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <Cell label={t("quant.equity")} value={`$${fmtUsd(sentinel?.equity)}`} />
                  <Cell label={t("quant.drawdown")} value={fmtPct(sentinel?.drawdown)} accent={(sentinel?.drawdown ?? 0) >= 0.03 ? "text-danger" : undefined} />
                  <Cell label={t("quant.var")} value={fmtPct(sentinel?.var)} accent={(sentinel?.var ?? 0) > 0.03 ? "text-warn" : undefined} />
                  <Cell label={t("quant.latency")} value={`${fmtNum(sentinel?.latency_ms, 1)} ms`} accent={(sentinel?.latency_ms ?? 0) > 300 ? "text-danger" : undefined} />
                </div>
                {sentinel?.breaker_reason ? (
                  <p className="mt-2 rounded-lg border border-line bg-base px-3 py-2 font-mono text-[11px] text-muted">
                    <span className="text-faint">{t("quant.reason")} </span>
                    {sentinel.breaker_reason}
                  </p>
                ) : null}
              </section>

              <div className="min-h-0 flex-1">
                <QuantControls />
              </div>
            </div>
          </div>
        </div>

        {/* Right flank — persistent System Event Log, anchored & independently scrollable */}
        <aside className="quant-log-flank flex min-h-[18rem] flex-col lg:min-h-0">
          <EventLogTerminal events={events} dock />
        </aside>
      </div>
    </div>
  );
}

function Badge({
  label,
  value,
  className = "text-ink",
  pulse,
  extra,
}: {
  label: string;
  value: string;
  className?: string;
  pulse?: boolean;
  extra?: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-elevated px-2.5 py-1">
      <p className="text-[9px] uppercase tracking-wider text-faint">{label}</p>
      <p className={`flex items-center gap-1 text-xs font-semibold ${className}`}>
        {pulse ? <FontAwesomeIcon icon={faCircleDot} className="h-2 w-2 animate-pulse" /> : null}
        <span className="whitespace-nowrap">{value}</span>
        {extra ? <span className="text-faint">{extra}</span> : null}
      </p>
    </div>
  );
}

function Cell({
  label,
  value,
  large,
  accent,
}: {
  label: string;
  value: string;
  large?: boolean;
  accent?: string;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-faint line-clamp-2">{label}</p>
      <p className={`mt-0.5 font-mono font-semibold tabular-nums ${large ? "text-xl" : "text-base"} ${accent ?? "text-ink"}`}>
        {value}
      </p>
    </div>
  );
}

function MiniTable({
  title,
  children,
  fill,
}: {
  title: string;
  children: React.ReactNode;
  fill?: boolean;
}) {
  return (
    <div
      className={[
        "rounded-lg border border-line bg-elevated p-2.5",
        fill ? "flex h-full min-h-0 flex-col" : "",
      ].join(" ")}
    >
      <p className="mb-1.5 shrink-0 text-[10px] uppercase tracking-wider text-faint">{title}</p>
      <div className={fill ? "min-h-0 flex-1 space-y-1" : "space-y-1"}>{children}</div>
    </div>
  );
}

function Row({
  left,
  right,
  rightClass = "text-ink",
}: {
  left: string;
  right: string;
  rightClass?: string;
}) {
  return (
    <div className="flex justify-between gap-2 font-mono">
      <span className="truncate text-muted">{left}</span>
      <span className={`shrink-0 ${rightClass}`}>{right}</span>
    </div>
  );
}

function ExecutionBadge({
  routing,
  detail,
  testnet,
}: {
  routing: ExecutionRouting;
  detail: string;
  testnet: boolean;
}) {
  const [showDetail, setShowDetail] = useState(false);
  const style = EXEC_STYLE[routing] ?? EXEC_STYLE.OFFLINE;
  const label = testnet && routing === "LIVE" ? "TESTNET LIVE" : routing;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setShowDetail((v) => !v)}
        onMouseEnter={() => setShowDetail(true)}
        onMouseLeave={() => setShowDetail(false)}
        className={`rounded-lg border px-2.5 py-1 transition-colors ${style.bg} ${style.border}`}
      >
        <p className="text-[9px] uppercase tracking-wider text-faint">
          <FontAwesomeIcon icon={faSatelliteDish} className="mr-1 h-2.5 w-2.5" />
          EXEC
        </p>
        <p className={`flex items-center gap-1 text-xs font-semibold ${style.color}`}>
          {style.pulse ? (
            <FontAwesomeIcon icon={faCircleDot} className="h-2 w-2 animate-pulse" />
          ) : null}
          <span className="whitespace-nowrap">{label}</span>
        </p>
      </button>
      {showDetail && detail ? (
        <div className="absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-line bg-surface p-2.5 font-mono text-[10px] leading-relaxed text-muted shadow-lg">
          {detail}
        </div>
      ) : null}
    </div>
  );
}
