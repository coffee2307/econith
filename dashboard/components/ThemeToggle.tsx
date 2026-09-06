"use client";

import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faMoon, faSun } from "@fortawesome/free-solid-svg-icons";
import { useTheme } from "@/contexts/ThemeContext";
import { useLocale } from "@/contexts/LocaleContext";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const { t } = useLocale();

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={theme === "dark" ? t("common.lightMode") : t("common.darkMode")}
      className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-surface text-muted transition-colors hover:bg-elevated hover:text-ink"
    >
      <FontAwesomeIcon
        icon={theme === "dark" ? faSun : faMoon}
        className="h-3.5 w-3.5"
      />
    </button>
  );
}
