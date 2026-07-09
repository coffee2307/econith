import type { TabId } from "@/constants/macroFeatures";

export type Locale = "en" | "vi";

export type SimMessageKey =
  | "unrest"
  | "proxyOverride"
  | "hubAdjust"
  | "proxyContagion"
  | "tariffChange"
  | "supplyDiversion";

export interface Dictionary {
  common: {
    go: string;
    day: string;
    growth: string;
    gdp: string;
    population: string;
    area: string;
    tier: string;
    all: string;
    noEvents: string;
    filterPlaceholder: string;
    loadingGlobe: string;
    play: string;
    pause: string;
    lightMode: string;
    darkMode: string;
    language: string;
    english: string;
    vietnamese: string;
    close: string;
  };
  nav: {
    overview: string;
    quant: string;
    world: string;
    worldSubtitle: string;
  };
  footer: {
    tagline: string;
    sentinel: string;
    internal: string;
  };
  connection: {
    live: string;
    connecting: string;
    reconnecting: string;
    offline: string;
  };
  landing: {
    badge: string;
    heroTitle: string;
    heroDesc: string;
    enterQuant: string;
    enterWorld: string;
    platformEyebrow: string;
    platformTitle: string;
    platformDesc: string;
    researchEyebrow: string;
    researchTitle: string;
    researchDesc: string;
    stats: {
      tickEngine: { value: string; label: string };
      modes: { value: string; label: string };
      agents: { value: string; label: string };
      sources: { value: string; label: string };
    };
    pipeline: {
      eyebrow: string;
      title: string;
      desc: string;
      steps: [string, string, string, string, string];
    };
    cta: {
      title: string;
      desc: string;
    };
    cards: {
      dataSpine: { title: string; desc: string };
      multiAgent: { title: string; desc: string };
      sentinel: { title: string; desc: string };
      digitalTwin: { title: string; desc: string };
      timeEngine: { title: string; desc: string };
      llmScenario: { title: string; desc: string };
      regimeDetection: { title: string; desc: string };
      explainableAi: { title: string; desc: string };
      antiOverfit: { title: string; desc: string };
    };
  };
  quant: {
    eyebrow: string;
    title: string;
    description: string;
    price: string;
    mid: string;
    obi: string;
    volumeDelta: string;
    aiDecision: string;
    regime: string;
    action: string;
    direction: string;
    confidence: string;
    agentAllocation: string;
    featureAttribution: string;
    sentinelLayer: string;
    breaker: string;
    mode: string;
    equity: string;
    drawdown: string;
    latency: string;
    var: string;
    cvar: string;
    varMethod: string;
    reason: string;
    injectAnomaly: string;
    flashCrash: string;
    latencySpike: string;
    rearmSentinel: string;
    eventLogTitle: string;
    levels: Record<string, string>;
    mission: {
      title: string;
      codename: string;
      testBanner: string;
      testBannerDesc: string;
      wsLabel: string;
      symbolLabel: string;
    };
    cockpit: {
      title: string;
      altimeter: string;
      fuelGauge: string;
      flightLog: string;
      radar: string;
      noFills: string;
      realized: string;
      unrealized: string;
      winRate: string;
      sharpe: string;
      drawdown: string;
      equity: string;
      freeMargin: string;
      leverage: string;
      liquidation: string;
      notional: string;
      mode: string;
      ws: {
        connecting: string;
        open: string;
        reconnecting: string;
        closed: string;
      };
    };
    pipeline: {
      title: string;
      feed: string;
      features: string;
      ai: string;
      sentinel: string;
      router: string;
      live: string;
      sim: string;
    };
    deployment: {
      title: string;
      step1: string;
      step2: string;
      step3: string;
      step4: string;
      current: string;
    };
    readiness: {
      title: string;
      binance: string;
      aiModel: string;
      sentinelArm: string;
      capital: string;
      liveExec: string;
      ready: string;
      locked: string;
      active: string;
      pending: string;
    };
    telemetry: {
      title: string;
      spread: string;
      bidVol: string;
      askVol: string;
      trades: string;
      buyVol: string;
      sellVol: string;
    };
    alt: {
      title: string;
      funding: string;
      ttf: string;
      oi: string;
      oiChg: string;
      liq: string;
    };
    controls: {
      title: string;
      subtitle: string;
    };
    peakEquity: string;
    lastPrice: string;
    badges: {
      quant: string;
      exec: string;
      testnet: string;
    };
    resizeLog: string;
    routing: {
      title: string;
      maxLeg: string;
      biasMult: string;
      conf: string;
      legs: string;
      reduceOnly: string;
      awaitingPlan: string;
      errorPrefix: string;
      profiles: Record<string, string>;
      cols: {
        symbol: string;
        side: string;
        qty: string;
        wt: string;
        desk: string;
      };
    };
    debate: {
      title: string;
      analystCount: string;
      conviction: string;
      fusedVerdict: string;
      bias: string;
      noVerdict: string;
      alphaCandidate: string;
      noAlpha: string;
    };
    dataInflow: {
      title: string;
      online: string;
      awaitingMacro: string;
      providerHealth: string;
    };
    enums: {
      action: Record<string, string>;
      verdict: Record<string, string>;
      side: Record<string, string>;
      quantMode: Record<string, string>;
      execRouting: Record<string, string>;
      breaker: Record<string, string>;
      sentinelMode: Record<string, string>;
      vendorStatus: Record<string, string>;
    };
  };
  world: {
    searchPlaceholder: string;
    adjustMetrics: string;
    coreHubs: string;
    coreHub: string;
    proxyNode: string;
    tracks: string;
    showAllFeatures: string;
    releaseOverrides: string;
    globalEvents: string;
    queued: string;
    waitingEvents: string;
    metricDetails: string;
    liveFeatures: string;
    noSimulation: string;
    selectNation: string;
    globeHint: string;
    inspectFeatures: string;
    imposeTariffOn: string;
    tariffRate: string;
    imposeTariff: string;
    fullMacroVector: string;
    modalSubtitle: string;
    overrideTitle: string;
    draftTitle: string;
    apply: string;
    discardDrafts: string;
    referenceOnly: string;
  };
  macro: {
    tabs: Record<TabId, string>;
    features: Record<string, string>;
  };
  simEvents: Record<SimMessageKey, string>;
  simSources: Record<string, string>;
  simDirs: { rose: string; eased: string };
  simTariff: { raised: string; cut: string; fall: string; recover: string };
  continents: Record<string, string>;
  countries: Record<string, string>;
}
