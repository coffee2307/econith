"use client";

import Link from "next/link";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faChartLine,
  faEarthAmericas,
  faArrowRight,
  faGaugeHigh,
} from "@fortawesome/free-solid-svg-icons";
import { MainControlDashboard } from "@/components/MainControlDashboard";
import { JournalistTicker } from "@/components/JournalistTicker";
import { useLocale } from "@/contexts/LocaleContext";

export function LandingPage() {
  const { locale } = useLocale();
  const vi = locale === "vi";

  return (
    <main className="mx-auto flex min-h-[calc(100vh-3.5rem)] w-full max-w-7xl flex-col gap-4 p-4 sm:p-6">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-4">
        <div>
          <div className="flex items-center gap-2">
            <FontAwesomeIcon icon={faGaugeHigh} className="h-4 w-4 text-accent" />
            <h1 className="text-lg font-bold tracking-tight text-ink">
              ECONITH CONTROL
            </h1>
          </div>
          <p className="mt-1 text-xs text-muted">
            {vi
              ? "Trạng thái và điều khiển vận hành hệ thống"
              : "System operations and runtime control"}
          </p>
        </div>

        <nav className="flex gap-2">
          <Link
            href="/quant"
            className="inline-flex items-center gap-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-xs font-semibold text-accent hover:bg-accent/20"
          >
            <FontAwesomeIcon icon={faChartLine} className="h-3.5 w-3.5" />
            Quant
            <FontAwesomeIcon icon={faArrowRight} className="h-3 w-3" />
          </Link>
          <Link
            href="/world"
            className="inline-flex items-center gap-2 rounded-lg border border-world/40 bg-world/10 px-3 py-2 text-xs font-semibold text-world hover:bg-world/20"
          >
            <FontAwesomeIcon icon={faEarthAmericas} className="h-3.5 w-3.5" />
            World
            <FontAwesomeIcon icon={faArrowRight} className="h-3 w-3" />
          </Link>
        </nav>
      </header>

      <MainControlDashboard />
      <JournalistTicker />
    </main>
  );
}
