"use client";

import { useEffect, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faUsers } from "@fortawesome/free-solid-svg-icons";
import { API_BASE } from "@/lib/api";
import { useLocale } from "@/contexts/LocaleContext";

const SOCIAL_UI_URL =
  process.env.NEXT_PUBLIC_SOCIAL_UI_URL ?? "http://localhost:3001";

export default function EconithSocial() {
  const { t } = useLocale();
  const [status, setStatus] = useState<"loading" | "ready" | "offline">("loading");
  const [detail, setDetail] = useState<string>("");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/social/health`);
        const data = await res.json();
        if (cancelled) return;
        if (res.ok && data.reachable) {
          setStatus("ready");
          setDetail("");
        } else {
          setStatus("offline");
          setDetail(data.error ?? t("social.offlineHint"));
        }
      } catch {
        if (!cancelled) {
          setStatus("offline");
          setDetail(t("social.offlineHint"));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [t]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-base">
      <div className="flex shrink-0 items-center justify-between gap-4 border-b border-line px-6 py-3">
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-elevated text-social">
            <FontAwesomeIcon icon={faUsers} className="h-4 w-4" />
          </span>
          <div>
            <h1 className="font-mono text-sm font-semibold text-ink">
              {t("nav.social")}
            </h1>
            <p className="text-xs text-muted">{t("social.subtitle")}</p>
          </div>
        </div>
        <span
          className={[
            "rounded-full px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide",
            status === "ready"
              ? "bg-emerald-500/10 text-emerald-600"
              : status === "loading"
                ? "bg-amber-500/10 text-amber-600"
                : "bg-red-500/10 text-red-600",
          ].join(" ")}
        >
          {status === "ready"
            ? t("social.statusReady")
            : status === "loading"
              ? t("social.statusLoading")
              : t("social.statusOffline")}
        </span>
      </div>

      {status === "offline" ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <p className="max-w-md text-sm text-muted">{detail}</p>
          <code className="rounded-lg border border-line bg-elevated px-3 py-2 font-mono text-xs text-faint">
            cd econith_social && npm run dev
          </code>
        </div>
      ) : (
        <iframe
          title="econith_social"
          src={SOCIAL_UI_URL}
          className="min-h-0 w-full flex-1 border-0 bg-base"
          allow="clipboard-read; clipboard-write"
        />
      )}
    </div>
  );
}
