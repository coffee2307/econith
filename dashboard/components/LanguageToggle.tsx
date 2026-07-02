"use client";

import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faGlobe } from "@fortawesome/free-solid-svg-icons";
import { useLocale } from "@/contexts/LocaleContext";
import type { Locale } from "@/lib/i18n/types";

export function LanguageToggle() {
  const { locale, setLocale, t } = useLocale();

  const next: Locale = locale === "en" ? "vi" : "en";
  const label = locale === "en" ? t("common.vietnamese") : t("common.english");

  return (
    <button
      type="button"
      onClick={() => setLocale(next)}
      aria-label={t("common.language")}
      title={label}
      className="flex h-8 items-center gap-1.5 rounded-xl border border-line bg-surface px-2.5 font-mono text-[11px] font-medium text-muted transition-colors hover:bg-elevated hover:text-ink"
    >
      <FontAwesomeIcon icon={faGlobe} className="h-3 w-3" />
      <span>{locale === "en" ? "EN" : "VI"}</span>
    </button>
  );
}
