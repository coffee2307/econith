"use client";

import Link from "next/link";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faChartLine,
  faEarthAmericas,
  faArrowRight,
  faBolt,
  faShieldHalved,
  faBrain,
  faNetworkWired,
  faGaugeHigh,
  faFlask,
  faMicroscope,
  faDiagramProject,
} from "@fortawesome/free-solid-svg-icons";
import { FeatureCard, SectionHeading } from "@/components/ui";
import { useLocale } from "@/contexts/LocaleContext";

export function LandingPage() {
  const { t } = useLocale();

  return (
    <div className="econith-grid mx-auto w-full max-w-7xl px-6">
      <section className="flex flex-col items-center py-24 text-center">
        <span className="mb-5 inline-flex items-center gap-2 rounded-xl border border-line bg-surface px-3 py-1 font-mono text-xs text-muted">
          <FontAwesomeIcon icon={faBolt} className="h-3 w-3 text-accent" />
          {t("landing.badge")}
        </span>

        <h1 className="max-w-3xl text-4xl font-semibold leading-tight tracking-tight text-ink sm:text-6xl">
          {t("landing.heroTitle")}
        </h1>

        <p className="mt-6 max-w-2xl text-base leading-relaxed text-muted">
          {t("landing.heroDesc")}
        </p>

        <div className="mt-10 flex flex-col gap-3 sm:flex-row">
          <Link href="/quant" className="btn-accent">
            <FontAwesomeIcon icon={faChartLine} className="h-4 w-4" />
            {t("landing.enterQuant")}
            <FontAwesomeIcon icon={faArrowRight} className="h-3.5 w-3.5" />
          </Link>
          <Link href="/world" className="btn-world">
            <FontAwesomeIcon icon={faEarthAmericas} className="h-4 w-4" />
            {t("landing.enterWorld")}
            <FontAwesomeIcon icon={faArrowRight} className="h-3.5 w-3.5" />
          </Link>
        </div>
      </section>

      <section className="border-t border-line py-20">
        <SectionHeading
          eyebrow={t("landing.platformEyebrow")}
          title={t("landing.platformTitle")}
          description={t("landing.platformDesc")}
        />

        <div className="mt-10 grid grid-cols-1 gap-4 md:grid-cols-3">
          <FeatureCard icon={faNetworkWired} title={t("landing.cards.dataSpine.title")}>
            {t("landing.cards.dataSpine.desc")}
          </FeatureCard>
          <FeatureCard icon={faBrain} title={t("landing.cards.multiAgent.title")}>
            {t("landing.cards.multiAgent.desc")}
          </FeatureCard>
          <FeatureCard icon={faShieldHalved} title={t("landing.cards.sentinel.title")}>
            {t("landing.cards.sentinel.desc")}
          </FeatureCard>
          <FeatureCard icon={faEarthAmericas} title={t("landing.cards.digitalTwin.title")}>
            {t("landing.cards.digitalTwin.desc")}
          </FeatureCard>
          <FeatureCard icon={faGaugeHigh} title={t("landing.cards.timeEngine.title")}>
            {t("landing.cards.timeEngine.desc")}
          </FeatureCard>
          <FeatureCard icon={faDiagramProject} title={t("landing.cards.llmScenario.title")}>
            {t("landing.cards.llmScenario.desc")}
          </FeatureCard>
        </div>
      </section>

      <section className="border-t border-line py-20">
        <SectionHeading
          eyebrow={t("landing.researchEyebrow")}
          title={t("landing.researchTitle")}
          description={t("landing.researchDesc")}
        />

        <div className="mt-10 grid grid-cols-1 gap-4 md:grid-cols-3">
          <FeatureCard icon={faMicroscope} title={t("landing.cards.regimeDetection.title")}>
            {t("landing.cards.regimeDetection.desc")}
          </FeatureCard>
          <FeatureCard icon={faFlask} title={t("landing.cards.explainableAi.title")}>
            {t("landing.cards.explainableAi.desc")}
          </FeatureCard>
          <FeatureCard icon={faChartLine} title={t("landing.cards.antiOverfit.title")}>
            {t("landing.cards.antiOverfit.desc")}
          </FeatureCard>
        </div>
      </section>
    </div>
  );
}
