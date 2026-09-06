"use client";

import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faGithub } from "@fortawesome/free-brands-svg-icons";
import { faShieldHalved } from "@fortawesome/free-solid-svg-icons";
import { useLocale } from "@/contexts/LocaleContext";

export function Footer() {
  const { t } = useLocale();

  return (
    <footer className="mt-auto border-t border-line bg-base">
      <div className="mx-auto flex w-full max-w-7xl flex-col items-center justify-between gap-3 px-6 py-8 text-sm text-faint sm:flex-row">
        <span className="font-mono">{t("footer.tagline")}</span>
        <div className="flex items-center gap-5">
          <span className="flex items-center gap-2">
            <FontAwesomeIcon icon={faShieldHalved} className="h-3.5 w-3.5" />
            {t("footer.sentinel")}
          </span>
          <span className="flex items-center gap-2">
            <FontAwesomeIcon icon={faGithub} className="h-3.5 w-3.5" />
            {t("footer.internal")}
          </span>
        </div>
      </div>
    </footer>
  );
}
