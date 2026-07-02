"""ECONITH :: ai.inference.predictor

Real-time inference engine (master plan, Phase 2).

Subscribes to the derived indicator topics on the EventBus, maintains a live
feature vector, and on a fixed cadence:

    1. classifies the market regime (regime switcher),
    2. polls every agent for a directional signal,
    3. fuses the signals into one portfolio decision (regime-weighted),
    4. builds a SHAP-style attribution map,
    5. publishes ``ai.signal`` for the bridge / dashboard.

The Sentinel retains veto power downstream: ``ai.signal`` is advisory only --
the execution bridge refuses to act on it whenever Sentinel mode != NORMAL.

World -> Quant coupling
-----------------------
The predictor also subscribes to ``world.micro_impact`` -- the Microstructural
Volatility Vector emitted by ECONITH World. It biases the *perceived* order-flow
imbalance and realised volatility and exerts additive log-pressure on the regime
distribution, so a macro/geopolitical shock (e.g. a 200% tariff barrier) can
force an HMM/GMM regime transition inside the Quant engine. The transient shock
relaxes back toward neutral over its ``duration_ticks``.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import asdict
from typing import Any

from ai.agents.agent_loaders import load_active_agents
from ai.agents.base import BaseAgent
from ai.agents.mean_reversion import MeanReversionAgent
from ai.agents.scalper import ScalperAgent
from ai.agents.trend import TrendAgent
from ai.ensemble.decision_fusion import fuse_signals
from ai.explainability.shap import attribution_to_json, build_attribution
from ai.regime.classifier import REGIMES, HeuristicRegimeClassifier, RegimeState
from ai.regime.regime import load_active_regime
from ai.regime.switcher import RegimeSwitcher
from core.event_bus import Event, EventBus
from core.mode import get_mode_manager

logger = logging.getLogger("econith.ai.inference")

# Canonical roster order the ensemble + switcher allocate against.
_ROSTER = ("trend", "mean_reversion", "scalper")


class Predictor:
    """Drives the agent ensemble from live features and emits ai.signal.

    On construction it tries to seat the TRAINED trading desks (the PPO
    checkpoints promoted by the deploy customs gate) and the TRAINED regime
    forecaster (the HMM bundle). Any brain that is missing -- because it hasn't
    been trained/deployed yet, or the ML stack isn't installed -- is transparently
    replaced by the original deterministic heuristic, so the live system is always
    fully operational whether or not the factory has shipped models yet.
    """

    def __init__(
        self,
        bus: EventBus,
        agents: list[BaseAgent] | None = None,
        interval_s: float = 1.0,
    ) -> None:
        self._bus = bus
        self._agents, self._agent_brain = self._assemble_desks(agents)
        self._classifier, self._regime_brain = self._assemble_regime()
        self._switcher = RegimeSwitcher()
        self._interval = interval_s
        self._features: dict[str, Any] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # World -> Quant microstructural coupling (decaying transient).
        # SOVEREIGNTY GUARANTEE: this coupling is only ever consumed while the
        # process is in SIMULATION mode. In REALITY mode the shock is blocked at
        # ingestion AND zeroed at consumption (defense in depth) so the live
        # trading brain is never corrupted by synthetic simulation metrics.
        self._impact: dict[str, Any] | None = None
        self._impact_left = 0
        self._impact_dur = 1
        self._mode = get_mode_manager()

    # -- brain assembly (trained checkpoints with heuristic fallback) ---------
    @staticmethod
    def _assemble_desks(
        override: list[BaseAgent] | None,
    ) -> tuple[list[BaseAgent], str]:
        """Seat trained PPO desks where available; fill the rest with stubs."""
        if override is not None:
            return override, "custom"
        defaults: dict[str, BaseAgent] = {
            "trend": TrendAgent(),
            "mean_reversion": MeanReversionAgent(),
            "scalper": ScalperAgent(),
        }
        trained = {a.name: a for a in load_active_agents()}
        roster = [trained.get(name, defaults[name]) for name in _ROSTER]
        brain = "trained" if trained else "heuristic"
        if trained:
            logger.info(
                "AI desks: %s",
                ", ".join(
                    f"{n}={'PPO' if n in trained else 'stub'}" for n in _ROSTER
                ),
            )
        else:
            logger.info("AI desks: heuristic stubs (no trained checkpoints found)")
        return roster, brain

    @staticmethod
    def _assemble_regime() -> tuple[Any, str]:
        """Load the trained regime forecaster, else the heuristic classifier."""
        trained = load_active_regime()
        if trained is not None:
            logger.info("regime forecaster: TRAINED (%s)", trained.method)
            return trained, "trained"
        logger.info("regime forecaster: heuristic")
        return HeuristicRegimeClassifier(), "heuristic"

    # -- lifecycle ------------------------------------------------------------
    def register(self) -> None:
        self._bus.subscribe("md.ticker", self._on_ticker)
        self._bus.subscribe("indicator.obi", self._on_obi)
        self._bus.subscribe("indicator.volume_delta", self._on_volume_delta)
        self._bus.subscribe("alt.open_interest", self._on_open_interest)
        self._bus.subscribe("alt.funding_rate", self._on_funding)
        self._bus.subscribe("alt.liquidation", self._on_liquidation)
        self._bus.subscribe("world.micro_impact", self._on_micro_impact)
        logger.info("predictor registered to feature + world-coupling topics")

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._bus.publish(
            "system.log", level="info", source="ai",
            message=(
                f"inference engine online ({len(self._agents)} agents, "
                f"desks={self._agent_brain}, regime={self._regime_brain})"
            ),
        )
        self._task = asyncio.create_task(self.run(), name="ai-predictor")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # -- feature ingestion ----------------------------------------------------
    # These handlers mirror the EXACT feature schema the Phase A collector wrote
    # to Parquet, so the live vector the trained desks receive matches training.
    async def _on_ticker(self, event: Event) -> None:
        price = event.payload.get("price")
        self._features.update(price=price, mid=event.payload.get("mid", price))

    async def _on_obi(self, event: Event) -> None:
        self._features.update(
            obi=event.payload.get("obi"),
            bid_volume=event.payload.get("bid_volume"),
            ask_volume=event.payload.get("ask_volume"),
            mid=event.payload.get("mid", self._features.get("mid")),
            best_bid=event.payload.get("best_bid"),
            best_ask=event.payload.get("best_ask"),
        )

    async def _on_volume_delta(self, event: Event) -> None:
        self._features.update(
            volume_delta=event.payload.get("volume_delta"),
            buy_volume=event.payload.get("buy_volume"),
            sell_volume=event.payload.get("sell_volume"),
            trade_count=event.payload.get("trade_count"),
        )

    async def _on_open_interest(self, event: Event) -> None:
        self._features.update(
            open_interest=event.payload.get("open_interest"),
            oi_change_pct=event.payload.get("oi_change_pct"),
        )

    async def _on_funding(self, event: Event) -> None:
        self._features.update(
            funding_rate=event.payload.get("funding_rate"),
            time_to_funding_s=event.payload.get("time_to_funding_s"),
        )

    async def _on_liquidation(self, event: Event) -> None:
        self._features.update(
            liquidation_notional=event.payload.get("total_notional"),
        )

    async def _on_micro_impact(self, event: Event) -> None:
        """Latch the latest World->Quant shock vector (resets its decay clock).

        REALITY-MODE HARD BLOCK: in REALITY mode the synthetic World shock is
        dropped at ingestion and any previously latched shock is cleared, so it
        can never reach the ensemble or the regime layer.
        """
        if not self._mode.coupling_enabled():
            self._impact = None
            self._impact_left = 0
            return
        self._impact = dict(event.payload)
        self._impact_dur = max(1, int(event.payload.get("duration_ticks", 1)))
        self._impact_left = self._impact_dur

    # -- world coupling helpers ----------------------------------------------
    def _impact_strength(self) -> float:
        """Remaining fraction of the transient shock in ``[0, 1]``.

        Returns 0.0 whenever coupling is disabled (REALITY mode), guaranteeing
        the coupled-feature and regime-pressure paths are exact no-ops.
        """
        if not self._mode.coupling_enabled():
            return 0.0
        if not self._impact or self._impact_left <= 0:
            return 0.0
        return self._impact_left / self._impact_dur

    def _coupled_features(self, k: float) -> dict[str, Any]:
        """A copy of the live features biased by the active world shock."""
        feats = dict(self._features)
        if not self._impact or k <= 0.0:
            return feats
        obi_shock = float(self._impact.get("order_flow_shock", 0.0)) * k
        vol_mult = 1.0 + (float(self._impact.get("volatility_multiplier", 1.0)) - 1.0) * k
        if feats.get("obi") is not None:
            feats["obi"] = max(-1.0, min(1.0, float(feats["obi"]) + obi_shock))
        if feats.get("volume_delta") is not None:
            feats["volume_delta"] = float(feats["volume_delta"]) * vol_mult
        return feats

    def _pressured_regime(self, regime: RegimeState, k: float) -> RegimeState:
        """Apply additive log-pressure from the world shock to the regime dist.

        ``p'[r] ∝ p[r] · exp(pressure[r]·k)`` -- this is what can *force* an
        HMM/GMM transition when macro conditions turn hostile.
        """
        if not self._impact or k <= 0.0:
            return regime
        pressure = self._impact.get("regime_pressure") or {}
        if not pressure:
            return regime
        base = regime.probabilities or {regime.label: 1.0}
        adjusted = {
            r: base.get(r, 0.0) * math.exp(float(pressure.get(r, 0.0)) * k)
            for r in REGIMES
        }
        total = sum(adjusted.values()) or 1.0
        probs = {r: v / total for r, v in adjusted.items()}
        label = max(probs, key=probs.get)
        return RegimeState(label=label, probabilities=probs,
                           method=f"{regime.method}+world")

    # -- inference loop -------------------------------------------------------
    async def run(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            if not self._features:
                continue

            k = self._impact_strength()
            coupled = self._coupled_features(k)

            regime = self._classifier.classify(coupled)
            regime = self._pressured_regime(regime, k)
            allocation = self._switcher.allocate(regime)
            signals = [agent.act(coupled) for agent in self._agents]
            decision = fuse_signals(signals, allocation)
            attribution = build_attribution(signals, allocation)
            explain = attribution_to_json(decision.action, decision.direction, attribution)

            await self._bus.publish(
                "ai.signal",
                **asdict(decision),
                regime_confidence=round(regime.confidence, 4),
                regime_method=regime.method,
                world_coupled=k > 0.0,
                mode=self._mode.mode.value,
                agent_brain=self._agent_brain,
                regime_brain=self._regime_brain,
                explain=explain,
            )

            # Relax the transient world shock toward neutral.
            if self._impact_left > 0:
                self._impact_left -= 1

    # -- introspection --------------------------------------------------------
    @property
    def feature_count(self) -> int:
        return len(self._features)
