"use client";

import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faCircleDot } from "@fortawesome/free-solid-svg-icons";
import type { ConnectionStatus } from "@/hooks/useMetricsStream";
import { useLocale } from "@/contexts/LocaleContext";

export function ConnectionBadge({
  status,
  attempts,
}: {
  status: ConnectionStatus;
  attempts?: number;
}) {
  const { t } = useLocale();

  const map: Record<ConnectionStatus, { label: string; color: string }> = {
    open: { label: t("connection.live"), color: "text-ok" },
    connecting: { label: t("connection.connecting"), color: "text-warn" },
    reconnecting: { label: t("connection.reconnecting"), color: "text-warn" },
    closed: { label: t("connection.offline"), color: "text-danger" },
  };

  const m = map[status];
  return (
    <span className="inline-flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-1 font-mono text-xs">
      <FontAwesomeIcon
        icon={faCircleDot}
        className={`h-2.5 w-2.5 ${m.color} ${
          status === "open" ? "animate-pulse" : ""
        }`}
      />
      <span className={m.color}>{m.label}</span>
      {status === "reconnecting" && attempts ? (
        <span className="text-faint">#{attempts}</span>
      ) : null}
    </span>
  );
}
