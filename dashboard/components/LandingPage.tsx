"use client";

import Link from "next/link";
import { useCallback, type MouseEvent } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
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
  faCamera,
  faBoltLightning,
  faScaleBalanced,
  faSatelliteDish,
  faWandMagicSparkles,
} from "@fortawesome/free-solid-svg-icons";
import { ScrollReveal } from "@/components/landing/ScrollReveal";
import { useLocale } from "@/contexts/LocaleContext";

function HeroBackground() {
  return (
    <div className="landing-hero-bg" aria-hidden>
      <div className="landing-grid" />
      <div className="landing-orb landing-orb-accent left-[8%] top-[12%] h-72 w-72" />
      <div className="landing-orb landing-orb-world right-[10%] top-[20%] h-80 w-80" />
      <div className="landing-orb landing-orb-violet left-1/2 top-[55%] h-64 w-64 -translate-x-1/2" />
    </div>
  );
}

function AnimatedCard({
  icon,
  title,
  children,
  accent = "accent",
  delay = 0,
}: {
  icon: IconDefinition;
  title: string;
  children: React.ReactNode;
  accent?: "accent" | "world";
  delay?: number;
}) {
  const onMove = useCallback((e: MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mouse-x", `${e.clientX - rect.left}px`);
    e.currentTarget.style.setProperty("--mouse-y", `${e.clientY - rect.top}px`);
  }, []);

  const iconColor = accent === "world" ? "text-world" : "text-accent";

  return (
    <ScrollReveal delay={delay} className="h-full">
      <div className="landing-card group h-full" onMouseMove={onMove}>
        <div className={`landing-card-icon ${iconColor}`}>
          <FontAwesomeIcon icon={icon} className="h-4 w-4 transition-colors duration-300" />
        </div>
        <h3 className="relative mt-4 text-base font-semibold text-ink transition-colors duration-300 group-hover:text-ink">
          {title}
        </h3>
        <p className="relative mt-2 text-sm leading-relaxed text-muted">{children}</p>
        <div
          className={[
            "absolute bottom-0 left-0 h-0.5 w-0 transition-all duration-500 group-hover:w-full",
            accent === "world" ? "bg-world" : "bg-accent",
          ].join(" ")}
        />
      </div>
    </ScrollReveal>
  );
}

function SectionHeading({
  eyebrow,
  title,
  description,
  centered = false,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  centered?: boolean;
}) {
  return (
    <ScrollReveal
      className={centered ? "mx-auto max-w-2xl text-center" : "max-w-2xl"}
      direction="up"
    >
      {eyebrow ? (
        <p className="mb-3 font-mono text-xs uppercase tracking-[0.22em] text-faint">
          {eyebrow}
        </p>
      ) : null}
      <h2 className="text-2xl font-semibold tracking-tight text-ink sm:text-4xl">{title}</h2>
      {description ? (
        <p className="mt-4 text-sm leading-relaxed text-muted sm:text-base">{description}</p>
      ) : null}
    </ScrollReveal>
  );
}

const PIPELINE_ICONS = [
  faCamera,
  faBoltLightning,
  faScaleBalanced,
  faEarthAmericas,
  faSatelliteDish,
] as const;

export function LandingPage() {
  const { t, dict } = useLocale();

  const platformCards = [
    { icon: faNetworkWired, title: t("landing.cards.dataSpine.title"), desc: t("landing.cards.dataSpine.desc") },
    { icon: faBrain, title: t("landing.cards.multiAgent.title"), desc: t("landing.cards.multiAgent.desc") },
    { icon: faShieldHalved, title: t("landing.cards.sentinel.title"), desc: t("landing.cards.sentinel.desc") },
    { icon: faEarthAmericas, title: t("landing.cards.digitalTwin.title"), desc: t("landing.cards.digitalTwin.desc"), accent: "world" as const },
    { icon: faGaugeHigh, title: t("landing.cards.timeEngine.title"), desc: t("landing.cards.timeEngine.desc") },
    { icon: faDiagramProject, title: t("landing.cards.llmScenario.title"), desc: t("landing.cards.llmScenario.desc"), accent: "world" as const },
  ];

  const researchCards = [
    { icon: faMicroscope, title: t("landing.cards.regimeDetection.title"), desc: t("landing.cards.regimeDetection.desc") },
    { icon: faFlask, title: t("landing.cards.explainableAi.title"), desc: t("landing.cards.explainableAi.desc") },
    { icon: faChartLine, title: t("landing.cards.antiOverfit.title"), desc: t("landing.cards.antiOverfit.desc") },
  ];

  const stats = [
    dict.landing.stats.tickEngine,
    dict.landing.stats.modes,
    dict.landing.stats.agents,
    dict.landing.stats.sources,
  ];

  const pipelineSteps = dict.landing.pipeline.steps;

  return (
    <div className="landing-page mx-auto w-full max-w-7xl px-4 sm:px-6">
      <HeroBackground />

      {/* ── Hero ─────────────────────────────────────────────────────── */}
      <section className="relative flex min-h-[88vh] flex-col items-center justify-center py-20 text-center sm:py-28">
        <ScrollReveal direction="down" delay={0}>
          <span className="landing-badge mb-6 inline-flex items-center gap-2 rounded-full border border-line bg-elevated px-4 py-1.5 font-mono text-xs text-muted backdrop-blur">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
            </span>
            <FontAwesomeIcon icon={faBolt} className="h-3 w-3 text-accent" />
            {t("landing.badge")}
          </span>
        </ScrollReveal>

        <ScrollReveal delay={80}>
          <h1 className="landing-title-gradient max-w-4xl text-4xl font-bold leading-[1.08] tracking-tight sm:text-6xl lg:text-7xl">
            {t("landing.heroTitle")}
          </h1>
        </ScrollReveal>

        <ScrollReveal delay={160}>
          <p className="mt-7 max-w-2xl text-base leading-relaxed text-muted sm:text-lg">
            {t("landing.heroDesc")}
          </p>
        </ScrollReveal>

        <ScrollReveal delay={240} className="mt-10 flex w-full flex-col items-center justify-center gap-4 sm:flex-row">
          <Link href="/quant" className="landing-btn-primary w-full sm:w-auto">
            <FontAwesomeIcon icon={faChartLine} className="h-4 w-4" />
            {t("landing.enterQuant")}
            <FontAwesomeIcon
              icon={faArrowRight}
              className="h-3.5 w-3.5 transition-transform duration-300 group-hover:translate-x-0.5"
            />
          </Link>
          <Link href="/world" className="landing-btn-secondary w-full sm:w-auto">
            <FontAwesomeIcon icon={faEarthAmericas} className="h-4 w-4 text-world" />
            {t("landing.enterWorld")}
            <FontAwesomeIcon icon={faArrowRight} className="h-3.5 w-3.5 text-muted" />
          </Link>
        </ScrollReveal>

        {/* Stats strip */}
        <div className="mt-16 grid w-full max-w-4xl grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
          {stats.map((stat, i) => (
            <ScrollReveal key={stat.label} delay={320 + i * 70} className="h-full">
              <div className="landing-stat h-full text-center">
                <p className="font-mono text-2xl font-bold tracking-tight text-ink sm:text-3xl">
                  {stat.value}
                </p>
                <p className="mt-1 text-[11px] uppercase tracking-wider text-faint sm:text-xs">
                  {stat.label}
                </p>
              </div>
            </ScrollReveal>
          ))}
        </div>
      </section>

      {/* ── 5-Phase Pipeline ─────────────────────────────────────────── */}
      <section className="border-t border-line py-20 sm:py-28">
        <SectionHeading
          eyebrow={t("landing.pipeline.eyebrow")}
          title={t("landing.pipeline.title")}
          description={t("landing.pipeline.desc")}
          centered
        />

        <div className="mt-12 flex flex-col items-stretch gap-3 md:flex-row md:items-center md:gap-0">
          {pipelineSteps.map((step, i) => (
            <div key={step} className="flex flex-1 items-center">
              <ScrollReveal delay={i * 90} direction="up" className="w-full flex-1">
                <div className="landing-pipeline-step">
                  <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-elevated font-mono text-xs font-bold text-accent">
                    {i + 1}
                  </span>
                  <FontAwesomeIcon
                    icon={PIPELINE_ICONS[i]}
                    className="h-4 w-4 text-muted transition-colors duration-300"
                  />
                  <span className="text-xs font-medium text-ink sm:text-sm">{step}</span>
                </div>
              </ScrollReveal>
              {i < pipelineSteps.length - 1 ? (
                <div className="landing-pipeline-connector mx-1" />
              ) : null}
            </div>
          ))}
        </div>
      </section>

      {/* ── Platform ─────────────────────────────────────────────────── */}
      <section className="border-t border-line py-20 sm:py-28">
        <SectionHeading
          eyebrow={t("landing.platformEyebrow")}
          title={t("landing.platformTitle")}
          description={t("landing.platformDesc")}
        />

        <div className="mt-12 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {platformCards.map((card, i) => (
            <AnimatedCard
              key={card.title}
              icon={card.icon}
              title={card.title}
              accent={card.accent}
              delay={i * 60}
            >
              {card.desc}
            </AnimatedCard>
          ))}
        </div>
      </section>

      {/* ── Research ─────────────────────────────────────────────────── */}
      <section className="border-t border-line py-20 sm:py-28">
        <SectionHeading
          eyebrow={t("landing.researchEyebrow")}
          title={t("landing.researchTitle")}
          description={t("landing.researchDesc")}
        />

        <div className="mt-12 grid grid-cols-1 gap-5 md:grid-cols-3">
          {researchCards.map((card, i) => (
            <AnimatedCard key={card.title} icon={card.icon} title={card.title} delay={i * 80}>
              {card.desc}
            </AnimatedCard>
          ))}
        </div>
      </section>

      {/* ── CTA ──────────────────────────────────────────────────────── */}
      <section className="border-t border-line py-20 sm:py-28">
        <ScrollReveal direction="up">
          <div className="landing-cta">
            <div className="landing-cta-glow" aria-hidden />
            <div className="relative">
              <span className="mb-4 inline-flex items-center gap-2 rounded-full border border-line bg-elevated/80 px-3 py-1 font-mono text-[10px] uppercase tracking-widest text-faint">
                <FontAwesomeIcon icon={faWandMagicSparkles} className="h-3 w-3 text-world" />
                ECONITH
              </span>
              <h2 className="text-2xl font-semibold tracking-tight text-ink sm:text-4xl">
                {t("landing.cta.title")}
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-sm leading-relaxed text-muted sm:text-base">
                {t("landing.cta.desc")}
              </p>
              <div className="mt-8 flex flex-col items-center justify-center gap-4 sm:flex-row">
                <Link href="/quant" className="landing-btn-primary w-full sm:w-auto">
                  <FontAwesomeIcon icon={faChartLine} className="h-4 w-4" />
                  {t("landing.enterQuant")}
                </Link>
                <Link href="/world" className="landing-btn-secondary w-full sm:w-auto">
                  <FontAwesomeIcon icon={faEarthAmericas} className="h-4 w-4 text-world" />
                  {t("landing.enterWorld")}
                </Link>
              </div>
            </div>
          </div>
        </ScrollReveal>
      </section>
    </div>
  );
}
