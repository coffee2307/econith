"use client";

/**
 * ECONITH Quant — "Sovereign Trading OS" War Room.
 *
 * Institutional command deck with a strict visual hierarchy:
 *   STATUS (top command bar) → ALPHA (AI ensemble + debate) →
 *   EXECUTION (smart routing) → RISK (Sentinel + data + operator) → LOG.
 */
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faShieldHalved,
  faBrain,
  faSatelliteDish,
} from "@fortawesome/free-solid-svg-icons";
import { useMetrics } from "@/components/MetricsProvider";
import { EventLogTerminal } from "@/components/EventLogTerminal";
import { QuantControls } from "@/components/QuantControls";
import { QuantCockpitHUD } from "@/components/quant/cockpit/QuantCockpitHUD";
import { QuantRoutingPanel } from "@/components/quant/QuantRoutingPanel";
import { AlphaDebateWidget } from "@/components/quant/AlphaDebateWidget";
import { DataInflowMonitor } from "@/components/quant/DataInflowMonitor";
import { Panel } from "@/components/quant/ui/Panel";
import { StatusBadge, type BadgeTone } from "@/components/quant/ui/StatusBadge";
import { MetricGauge } from "@/components/quant/ui/MetricGauge";
import { QuantResizeHandle } from "@/components/quant/ui/QuantResizeHandle";
import { useLocale } from "@/contexts/LocaleContext";
import { useQuantLogHeight } from "@/hooks/useQuantLogHeight";
import { tQuantEnum } from "@/lib/i18n/quantEnum";
import { fmtNum, fmtPct, fmtSigned, fmtUsd } from "@/lib/format";
import type { ConnectionStatus } from "@/hooks/useMetricsStream";
import { useExecutionStatus, type ExecutionRouting } from "@/hooks/useExecutionStatus";

const BREAKER_TONE: Record<string, BadgeTone> = {
  CLOSED: "ok",
  HALF_OPEN: "warn",
  OPEN: "danger",
};
const MODE_TONE: Record<string, BadgeTone> = {
  NORMAL: "ok",
  REDUCE_ONLY: "warn",
  FROZEN: "danger",
};
const CONN_TONE: Record<ConnectionStatus, BadgeTone> = {
  open: "ok",
  connecting: "warn",
  reconnecting: "warn",
  closed: "danger",
};
const EXEC_TONE: Record<ExecutionRouting, BadgeTone> = {
  LIVE: "ok",
  SYNTHETIC: "accent",
  DEGRADED: "warn",
  OFFLINE: "danger",
};

export function QuantMissionControl() {
  const { snapshot, status, attempts } = useMetrics();
  const { t } = useLocale();
  const { execution } = useExecutionStatus();
  const { height: logHeight, adjust: adjustLogHeight } = useQuantLogHeight();

  const market = snapshot?.market;
  const sentinel = snapshot?.sentinel;
  const ai = snapshot?.ai;
  const alt = snapshot?.alt;
  const quantEvents = snapshot?.events ?? [];
  const quantMode = snapshot?.quant_mode?.mode ?? "REALITY";
  const routing = snapshot?.routing;
  const debate = snapshot?.debate;
  const alpha = snapshot?.alpha;

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
    <div className="wr-room flex h-full min-h-0 w-full max-w-[100vw] flex-1 flex-col gap-2.5 overflow-hidden px-3 py-2.5 lg:px-5 lg:py-3">
      {/* ══ STATUS — top command bar ══════════════════════════════════════ */}
      <header className="flex flex-none flex-wrap items-center justify-between gap-3 rounded-lg border border-line bg-surface/70 px-3 py-2">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-zone-alpha/40 bg-zone-alpha/10">
            <FontAwesomeIcon icon={faSatelliteDish} className="h-3.5 w-3.5 text-zone-alpha" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="font-mono text-sm font-bold uppercase tracking-[0.14em] text-ink">
                {t("quant.mission.title")}
              </h1>
              <span className="rounded border border-warn/40 bg-warn/10 px-1.5 py-0.5 font-mono text-[8px] font-bold uppercase tracking-wider text-warn">
                {t("quant.mission.testBanner")}
              </span>
            </div>
            <p className="truncate font-mono text-[9px] uppercase tracking-[0.18em] text-faint">
              {t("quant.mission.codename")}
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge
            label={t("quant.mission.wsLabel")}
            value={connLabel}
            tone={CONN_TONE[status]}
            live={status === "open"}
            suffix={status === "reconnecting" && attempts ? `#${attempts}` : undefined}
          />
          <StatusBadge label={t("quant.breaker")} value={tQuantEnum(t, "breaker", breaker)} tone={BREAKER_TONE[breaker] ?? "neutral"} />
          <StatusBadge label={t("quant.mode")} value={tQuantEnum(t, "sentinelMode", mode)} tone={MODE_TONE[mode] ?? "neutral"} />
          <StatusBadge
            label={t("quant.badges.quant")}
            value={tQuantEnum(t, "quantMode", quantMode)}
            tone={quantMode === "REALITY" ? "ok" : "warn"}
            live={quantMode === "SIMULATION"}
          />
          {execution ? (
            <StatusBadge
              label={t("quant.badges.exec")}
              value={
                execution.testnet && execution.execution_routing === "LIVE"
                  ? t("quant.badges.testnet")
                  : tQuantEnum(t, "execRouting", execution.execution_routing)
              }
              tone={EXEC_TONE[execution.execution_routing] ?? "danger"}
              icon={faSatelliteDish}
              live={execution.execution_routing === "LIVE"}
              tooltip={execution.detail}
            />
          ) : null}
        </div>
      </header>

      {/* ══ Market micro-ticker ═══════════════════════════════════════════ */}
      <div className="flex flex-none flex-wrap items-stretch gap-2 rounded-lg border border-line bg-surface/60 px-3 py-2">
        <Ticker label={`${t("quant.price")} · ${market?.symbol ?? "—"}`} value={fmtUsd(market?.price)} lead />
        <TickerDivider />
        <Ticker label={t("quant.mid")} value={fmtUsd(market?.mid)} />
        <Ticker label={t("quant.telemetry.spread")} value={spread != null ? fmtUsd(spread, 4) : "—"} />
        <Ticker
          label={t("quant.obi")}
          value={fmtNum(market?.obi)}
          tone={(market?.obi ?? 0) > 0 ? "long" : (market?.obi ?? 0) < 0 ? "short" : undefined}
        />
        <Ticker
          label={t("quant.volumeDelta")}
          value={fmtSigned(market?.volume_delta)}
          tone={(market?.volume_delta ?? 0) > 0 ? "long" : (market?.volume_delta ?? 0) < 0 ? "short" : undefined}
        />
        <Ticker label={t("quant.telemetry.trades")} value={market?.trade_count?.toLocaleString() ?? "—"} />
      </div>

      {/* ══ Body + resizable log dock ═════════════════════════════════════ */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-2.5 overflow-hidden lg:grid-cols-[minmax(0,1fr)_22rem] xl:grid-cols-[minmax(0,1fr)_25rem]">
          {/* LEFT — scroll only inside this column */}
          <div className="flex min-h-0 flex-col gap-2.5 overflow-y-auto overflow-x-hidden overscroll-contain pr-0.5">
            {/* ALPHA zone — capped height; scroll inside panels, never eats routing */}
            <div className="grid max-h-[min(40vh,22rem)] min-h-[14rem] shrink-0 grid-cols-1 gap-2.5 overflow-hidden xl:grid-cols-2">
              <AiEnsemblePanel ai={ai} tLabel={t} className="h-full max-h-full min-h-0" />
              <AlphaDebateWidget debate={debate} alpha={alpha} className="h-full max-h-full min-h-0" />
            </div>

            <QuantRoutingPanel routing={routing} className="shrink-0" />
            <div className="shrink-0">
              <QuantCockpitHUD />
            </div>
          </div>

          {/* RIGHT — risk rail (stacked panels, scroll inside column) */}
          <aside className="flex min-h-0 flex-col gap-2.5 overflow-y-auto overflow-x-hidden overscroll-contain pr-0.5">
            <div className="shrink-0">
              <SentinelRiskPanel sentinel={sentinel} breaker={breaker} tLabel={t} />
            </div>
            <div className="shrink-0">
              <DataInflowMonitor market={market} alt={alt} quantOnly />
            </div>
            <div className="shrink-0 pb-1">
              <QuantControls />
            </div>
          </aside>
        </div>

        <QuantResizeHandle onResize={adjustLogHeight} label={t("quant.resizeLog")} />
        <div className="flex-none overflow-hidden" style={{ height: logHeight }}>
          <EventLogTerminal events={quantEvents} dock fill />
        </div>
      </div>
    </div>
  );
}

/* ── AI ensemble (ALPHA) ─────────────────────────────────────────────────── */
type TFn = (key: string) => string;

function AiEnsemblePanel({
  ai,
  tLabel,
  className = "",
}: {
  ai?: import("@/hooks/useMetricsStream").AiState;
  tLabel: TFn;
  className?: string;
}) {
  const action = ai?.action ?? "—";
  const actionDisplay = action === "—" ? "—" : tQuantEnum(tLabel, "action", action);
  const actionColor =
    action === "LONG" ? "text-long" : action === "SHORT" ? "text-short" : "text-muted";
  const actionTone = action === "LONG" ? "long" : action === "SHORT" ? "short" : "neutral";

  return (
    <Panel
      title={tLabel("quant.aiDecision")}
      icon={faBrain}
      zone="alpha"
      fill
      className={className}
      bodyClassName="flex min-h-0 flex-1 flex-col gap-2.5"
      right={
        <span className="wr-panel-eyebrow">
          {tLabel("quant.regime")} {ai?.regime ?? "—"} · {fmtPct(ai?.regime_confidence ?? 0, 0)}
        </span>
      }
    >
      <div className="flex shrink-0 items-center gap-3 rounded-md border border-line bg-base/40 p-2.5">
        <MetricGauge
          label={tLabel("quant.confidence")}
          value={fmtPct(ai?.confidence ?? 0, 0)}
          fraction={ai?.confidence ?? 0}
          tone={actionTone}
        />
        <div className="min-w-0 flex-1">
          <p className="wr-label">{tLabel("quant.action")}</p>
          <p className={`font-mono text-3xl font-bold leading-none ${actionColor}`}>{actionDisplay}</p>
          <p className="mt-1 font-mono text-[10px] text-muted">
            {tLabel("quant.direction")} {fmtSigned(ai?.direction, 3)}
          </p>
        </div>
      </div>

      <div className="grid min-h-[9rem] flex-1 grid-cols-2 gap-2">
        <MiniTable title={tLabel("quant.agentAllocation")}>
          {ai?.weights
            ? Object.entries(ai.weights).map(([k, w]) => (
                <Row key={k} left={k} right={fmtPct(w, 0)} />
              ))
            : <Empty />}
        </MiniTable>
        <MiniTable title={tLabel("quant.featureAttribution")}>
          {ai?.explain?.attribution?.length
            ? ai.explain.attribution.slice(0, 5).map((a) => (
                <Row
                  key={a.feature}
                  left={a.feature}
                  right={fmtSigned(a.importance, 3)}
                  rightClass={a.importance >= 0 ? "text-long" : "text-short"}
                />
              ))
            : <Empty />}
        </MiniTable>
      </div>
    </Panel>
  );
}

/* ── Sentinel risk (RISK) ────────────────────────────────────────────────── */
function SentinelRiskPanel({
  sentinel,
  breaker,
  tLabel,
}: {
  sentinel?: import("@/hooks/useMetricsStream").SentinelState;
  breaker: string;
  tLabel: TFn;
}) {
  const dd = sentinel?.drawdown ?? 0;
  const varv = sentinel?.var ?? 0;
  const lat = sentinel?.latency_ms ?? 0;

  return (
    <Panel
      title={tLabel("quant.sentinelLayer")}
      icon={faShieldHalved}
      zone="risk"
      bodyClassName="flex flex-col gap-2.5"
      right={
        <span
          className={`wr-panel-eyebrow ${
            breaker === "OPEN" ? "text-short" : breaker === "HALF_OPEN" ? "text-warn" : "text-long"
          }`}
        >
          {tQuantEnum(tLabel, "breaker", breaker)}
        </span>
      }
    >
      <div className="grid grid-cols-3 gap-1">
        <MetricGauge
          label={tLabel("quant.drawdown")}
          value={fmtPct(dd, 1)}
          fraction={Math.min(1, dd / 0.1)}
          tone={dd >= 0.03 ? "danger" : dd >= 0.015 ? "warn" : "ok"}
        />
        <MetricGauge
          label={tLabel("quant.var")}
          value={fmtPct(varv, 1)}
          fraction={Math.min(1, varv / 0.05)}
          tone={varv > 0.03 ? "warn" : "accent"}
        />
        <MetricGauge
          label={tLabel("quant.latency")}
          value={`${fmtNum(lat, 0)}`}
          sub="ms"
          fraction={Math.min(1, lat / 500)}
          tone={lat > 300 ? "danger" : lat > 150 ? "warn" : "ok"}
        />
      </div>

      <div className="grid grid-cols-2 gap-1.5">
        <Readout label={tLabel("quant.equity")} value={`$${fmtUsd(sentinel?.equity)}`} />
        <Readout label={tLabel("quant.peakEquity")} value={`$${fmtUsd(sentinel?.peak_equity)}`} />
      </div>

      {sentinel?.breaker_reason ? (
        <p className="rounded-md border border-line bg-base/50 px-2.5 py-1.5 font-mono text-[10px] leading-snug text-muted">
          <span className="text-faint">{tLabel("quant.reason")} </span>
          {sentinel.breaker_reason}
        </p>
      ) : (
        <div className="min-h-[2.25rem]" aria-hidden />
      )}
    </Panel>
  );
}

/* ── small shared bits ───────────────────────────────────────────────────── */
function Ticker({
  label,
  value,
  tone,
  lead,
}: {
  label: string;
  value: string;
  tone?: "long" | "short";
  lead?: boolean;
}) {
  const color = tone === "long" ? "text-long" : tone === "short" ? "text-short" : "text-ink";
  return (
    <div className="flex min-w-0 flex-col justify-center">
      <span className="wr-label truncate">{label}</span>
      <span className={`wr-value ${lead ? "text-lg" : "text-sm"} ${color}`}>{value}</span>
    </div>
  );
}

function TickerDivider() {
  return <div className="hidden w-px self-stretch bg-line sm:block" />;
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-line bg-elevated/50 px-2 py-1.5">
      <p className="wr-label">{label}</p>
      <p className="wr-value text-sm">{value}</p>
    </div>
  );
}

function MiniTable({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex min-h-0 flex-col rounded-md border border-line bg-elevated/40 p-2">
      <p className="wr-label mb-1 shrink-0">{title}</p>
      <div className="min-h-[6rem] flex-1 space-y-1 overflow-y-auto overscroll-contain">{children}</div>
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
    <div className="flex justify-between gap-2 font-mono text-[10px]">
      <span className="truncate text-muted">{left}</span>
      <span className={`shrink-0 ${rightClass}`}>{right}</span>
    </div>
  );
}

function Empty() {
  return <span className="font-mono text-[10px] text-faint">—</span>;
}
