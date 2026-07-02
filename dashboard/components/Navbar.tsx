"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faChartLine,
  faEarthAmericas,
  faCube,
} from "@fortawesome/free-solid-svg-icons";
import { useMetrics } from "@/components/MetricsProvider";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { ThemeToggle } from "@/components/ThemeToggle";
import { LanguageToggle } from "@/components/LanguageToggle";
import { useLocale } from "@/contexts/LocaleContext";

export function Navbar() {
  const pathname = usePathname();
  const { status, attempts } = useMetrics();
  const { t } = useLocale();
  const onWorld = pathname.startsWith("/world");

  const nav = [
    { href: "/", label: t("nav.overview"), icon: faCube },
    { href: "/quant", label: t("nav.quant"), icon: faChartLine },
    { href: "/world", label: t("nav.world"), icon: faEarthAmericas },
  ];

  return (
    <header className="sticky top-0 z-50 border-b border-line bg-base/90 backdrop-blur">
      <div className="mx-auto flex h-14 w-full max-w-7xl items-center justify-between gap-4 px-6">
        <Link href="/" className="flex shrink-0 items-center gap-2.5">
          <span className="flex h-7 w-7 items-center justify-center rounded-xl border border-line bg-elevated">
            <FontAwesomeIcon icon={faCube} className="h-3.5 w-3.5 text-accent" />
          </span>
          <span className="font-mono text-sm font-semibold tracking-tight text-ink">
            ECONITH
          </span>
        </Link>

        <nav className="flex items-center gap-1">
          {nav.map((item) => {
            const active =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            const accentClass =
              item.href === "/world"
                ? "text-world"
                : item.href === "/quant"
                  ? "text-accent"
                  : "text-ink";
            return (
              <Link
                key={item.href}
                href={item.href}
                className={[
                  "flex items-center gap-2 rounded-xl px-3 py-1.5 text-sm transition-colors",
                  active
                    ? `bg-elevated ${accentClass}`
                    : "text-muted hover:bg-elevated hover:text-ink",
                ].join(" ")}
              >
                <FontAwesomeIcon icon={item.icon} className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-2">
          <ConnectionBadge status={status} attempts={attempts} />
          <LanguageToggle />
          <ThemeToggle />
        </div>
      </div>

      {onWorld ? (
        <div className="border-t border-line bg-surface/80">
          <div className="mx-auto flex h-9 w-full max-w-7xl items-center px-6">
            <p className="font-mono text-[11px] text-faint">
              {t("nav.worldSubtitle")}
            </p>
          </div>
        </div>
      ) : null}
    </header>
  );
}
