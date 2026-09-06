"use client";

/**
 * ECONITH :: WarRoom Panel primitive
 *
 * The single card container for the Sovereign Trading OS. Enforces the
 * institutional hierarchy via an optional `zone` accent rail (alpha / exec /
 * risk) and a standardized header (title + right-slot eyebrow/actions).
 */
import type { ReactNode } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";

export type PanelZone = "alpha" | "exec" | "risk" | "neutral";

const ZONE_RAIL: Record<PanelZone, string> = {
  alpha: "wr-panel-rail wr-rail-alpha",
  exec: "wr-panel-rail wr-rail-exec",
  risk: "wr-panel-rail wr-rail-risk",
  neutral: "",
};

const ZONE_ICON: Record<PanelZone, string> = {
  alpha: "text-zone-alpha",
  exec: "text-zone-exec",
  risk: "text-zone-risk",
  neutral: "text-accent",
};

export function Panel({
  title,
  icon,
  zone = "neutral",
  right,
  fill = false,
  bodyClassName = "",
  className = "",
  children,
}: {
  title?: string;
  icon?: IconDefinition;
  zone?: PanelZone;
  right?: ReactNode;
  /** When true, panel body expands to fill parent height (use with h-full). */
  fill?: boolean;
  bodyClassName?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section
      className={[
        "wr-panel overflow-hidden",
        ZONE_RAIL[zone],
        className,
      ].join(" ")}
    >
      {title ? (
        <header className="wr-panel-head">
          <h2 className="wr-panel-title">
            {icon ? (
              <FontAwesomeIcon icon={icon} className={`h-3.5 w-3.5 ${ZONE_ICON[zone]}`} />
            ) : null}
            {title}
          </h2>
          {right ? <div className="flex items-center gap-2">{right}</div> : null}
        </header>
      ) : null}
      <div
        className={[
          "wr-panel-body",
          fill ? "wr-panel-body-fill" : "",
          bodyClassName,
        ].join(" ")}
      >
        {children}
      </div>
    </section>
  );
}
