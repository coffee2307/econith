"use client";

/**
 * ECONITH :: AlphaDebateWidget  (ALPHA zone)
 */
import { faComments } from "@fortawesome/free-solid-svg-icons";
import { fmtPct, fmtSigned } from "@/lib/format";
import type { AlphaState, DebateState } from "@/hooks/useMetricsStream";
import { Panel } from "@/components/quant/ui/Panel";
import { MetricGauge } from "@/components/quant/ui/MetricGauge";
import { SignalBar } from "@/components/quant/ui/SignalBar";
import { useLocale } from "@/contexts/LocaleContext";
import { tQuantEnum } from "@/lib/i18n/quantEnum";
import type { TranslateFn } from "@/lib/i18n/translate";

function verdictLabel(t: TranslateFn, bias: number): { text: string; tone: string } {
  if (bias > 0.05) return { text: tQuantEnum(t, "verdict", "LONG"), tone: "long" };
  if (bias < -0.05) return { text: tQuantEnum(t, "verdict", "SHORT"), tone: "short" };
  return { text: tQuantEnum(t, "verdict", "NEUTRAL"), tone: "neutral" };
}

function biasColor(bias: number): string {
  if (bias > 0.05) return "text-long";
  if (bias < -0.05) return "text-short";
  return "text-muted";
}

export function AlphaDebateWidget({
  debate,
  alpha,
  className = "",
}: {
  debate?: DebateState;
  alpha?: AlphaState;
  className?: string;
}) {
  const { t } = useLocale();
  const votes = debate?.votes ?? [];
  const consensusBias = debate?.consensus_bias ?? 0;
  const consensusConf = debate?.consensus_confidence ?? 0;
  const verdict = verdictLabel(t, consensusBias);
  const sources = debate?.sources ?? [];

  return (
    <Panel
      title={t("quant.debate.title")}
      icon={faComments}
      zone="alpha"
      fill
      className={className}
      bodyClassName="flex min-h-0 flex-1 flex-col gap-2.5"
      right={
        <span className="wr-panel-eyebrow">
          {t("quant.debate.analystCount", { count: votes.length })}
        </span>
      }
    >
      <div className="flex shrink-0 items-center gap-3 rounded-md border border-line bg-base/40 p-2.5">
        <MetricGauge
          label={t("quant.debate.conviction")}
          value={fmtPct(consensusConf, 0)}
          fraction={consensusConf}
          tone={verdict.tone}
        />
        <div className="min-w-0 flex-1">
          <p className="wr-label">{t("quant.debate.fusedVerdict")}</p>
          <p className={`font-mono text-2xl font-bold leading-none ${biasColor(consensusBias)}`}>
            {verdict.text}
          </p>
          <p className="mt-1 font-mono text-[10px] text-muted">
            {t("quant.debate.bias")} {fmtSigned(consensusBias, 3)}
          </p>
          <div className="mt-2">
            <SignalBar value={consensusBias} height={7} />
          </div>
          {sources.length ? (
            <div className="mt-2 flex flex-wrap gap-1">
              {sources.slice(0, 4).map((s) => (
                <span
                  key={s}
                  className="rounded border border-line px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-wider text-faint"
                >
                  {s}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-0.5">
        {votes.length ? (
          votes.map((v) => (
            <div key={v.agent} className="rounded-md border border-line bg-elevated/50 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] font-semibold text-ink">{v.agent}</span>
                <span className={`shrink-0 font-mono text-[11px] font-bold ${biasColor(v.bias)}`}>
                  {fmtSigned(v.bias, 3)}
                </span>
              </div>
              <div className="mt-1.5">
                <SignalBar value={v.bias} />
              </div>
              <div className="mt-1.5 flex items-center gap-2">
                <div className="h-1 flex-1 overflow-hidden rounded-full bg-faint/20">
                  <div
                    className="h-full rounded-full bg-zone-alpha"
                    style={{ width: `${Math.min(100, v.confidence * 100)}%` }}
                  />
                </div>
                <span className="shrink-0 font-mono text-[9px] text-faint">
                  {fmtPct(v.confidence, 0)}
                </span>
              </div>
              {v.rationale ? (
                <p className="mt-1 truncate font-mono text-[9px] leading-snug text-faint">
                  {v.rationale}
                </p>
              ) : null}
            </div>
          ))
        ) : (
          <p className="py-6 text-center text-[11px] text-faint">{t("quant.debate.noVerdict")}</p>
        )}
      </div>

      <div className="shrink-0 rounded-md border border-line bg-base/40 px-2.5 py-1.5">
        {alpha?.direction != null ? (
          <div className="flex items-center justify-between">
            <span className="wr-label">{t("quant.debate.alphaCandidate")}</span>
            <span className="font-mono text-[11px]">
              <span className="text-ink">{alpha.symbol ?? "—"}</span>{" "}
              <span className={biasColor(alpha.direction)}>{fmtSigned(alpha.direction, 3)}</span>{" "}
              <span className="text-faint">· {fmtPct(alpha.confidence ?? 0, 0)}</span>
            </span>
          </div>
        ) : (
          <p className="wr-label text-center">{t("quant.debate.noAlpha")}</p>
        )}
      </div>
    </Panel>
  );
}
