"""ECONITH :: ai.simulator_engine.reaction_models

Country reaction models (master plan, Phase 7 -- agent behaviour).

Each tick, every country is "best-responding" to the current world via a set of
injectable :class:`ReactionModel` agents. Each agent observes the full
:class:`WorldState` and proposes a list of :class:`Adjustment`s (deltas) to its
own variables. The kernel applies all proposals simultaneously, so the system
relaxes toward a Nash-style equilibrium over successive ticks.

DEPENDENCY INJECTION / H200 SWAP POINT
--------------------------------------
``ReactionModel`` is an ABC. The default heuristic agents (Central Bank, Trade
Ministry, Sentiment) implement transparent state-transition math. To plug in a
trained policy later, implement the SAME interface backed by a neural net or an
LLM agent pipeline and inject it into ``WorldKernel(models=[...])`` -- no other
code changes. ``NeuralReactionModel`` is a ready stub demonstrating the seam.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai.simulator_engine.macro_vectors import WorldState

logger = logging.getLogger("econith.ai.reaction_models")


@dataclass(slots=True)
class Adjustment:
    """A proposed change to one variable of one country.

    ``group == "tariff"`` is special: ``field`` is the *target* country code and
    ``delta`` is the change to ``tariffs[code][field]``.
    """

    code: str
    group: str
    field: str
    delta: float
    reason: str = ""
    event: str | None = None       # human-readable event text (optional)
    event_level: str = "info"      # info | ok | warn | danger


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class ReactionModel(ABC):
    """Abstract agent. Concrete models implement :meth:`react`."""

    name: str = "base"

    @abstractmethod
    def react(self, code: str, world: WorldState) -> list[Adjustment]:
        """Propose adjustments for ``code`` given the current ``world``."""
        raise NotImplementedError


# ===========================================================================
#  Central Bank -- monetary policy (Taylor-rule style)
# ===========================================================================
class CentralBankModel(ReactionModel):
    name = "central_bank"

    def __init__(self, neutral_rate: float = 0.025, step: float = 0.0015) -> None:
        self._neutral = neutral_rate
        self._step = step

    def react(self, code: str, world: WorldState) -> list[Adjustment]:
        c = world.countries[code]
        m = c.monetary
        adj: list[Adjustment] = []

        # Taylor rule: target = neutral + 1.5(inflation gap) + 0.5(growth gap)
        infl_gap = m.inflation_cpi - m.inflation_target
        growth_gap = c.gdp_growth - 0.025
        target = self._neutral + 1.5 * infl_gap + 0.5 * growth_gap
        rate_delta = _clamp(target - m.interest_rate, -self._step, self._step)
        if abs(rate_delta) > 1e-5:
            # Physics-only: do NOT attach English headline templates. Rate moves
            # surface through Tier-1 dialogue / grounded narratives when material.
            adj.append(
                Adjustment(
                    code,
                    "monetary",
                    "interest_rate",
                    rate_delta,
                    reason="taylor_rule",
                )
            )

        # Inflation responds to the real rate (cooling) with persistence.
        real_rate = m.interest_rate - m.inflation_cpi
        infl_delta = _clamp(-0.10 * real_rate - 0.05 * (m.inflation_cpi - m.inflation_target),
                            -0.004, 0.004)
        adj.append(Adjustment(code, "monetary", "inflation_cpi", infl_delta,
                              reason="inflation_dynamics"))

        # FX: relative rate differential vs world average pulls the currency.
        avg_rate = sum(s.monetary.interest_rate for s in world.countries.values()) / max(
            1, len(world.countries))
        fx_pull = -0.002 * (m.interest_rate - avg_rate) * m.fx_spot
        adj.append(Adjustment(code, "monetary", "fx_spot", fx_pull, reason="fx_carry"))
        return adj


# ===========================================================================
#  Trade Ministry -- tariffs, retaliation, supply-chain diversion
# ===========================================================================
class TradeMinistryModel(ReactionModel):
    name = "trade_ministry"

    def __init__(
        self,
        retaliation_speed: float = 0.4,
        deescalation_pull: float = 0.02,
        diversion_gain: float = 6.0,
    ) -> None:
        self._retal = retaliation_speed
        self._deesc = deescalation_pull
        self._diversion = diversion_gain

    def react(self, code: str, world: WorldState) -> list[Adjustment]:
        c = world.countries[code]
        adj: list[Adjustment] = []
        others = [o for o in world.codes() if o != code]

        export_pressure = 0.0
        for other in others:
            incoming = world.tariff(other, code)   # tariff `other` imposes on us
            ours = world.tariff(code, other)        # tariff we impose on them
            trust = world.alliance(code, other)

            # Retaliation: match aggression, scaled by distrust.
            gap = incoming - ours
            if gap > 0.01:
                retal = self._retal * gap * (1.0 - trust)
                if retal > 0.005:
                    adj.append(Adjustment(
                        code, "tariff", other, retal, reason="retaliation",
                    ))
            # De-escalation: if both sides high and we're hurting, negotiate down.
            elif ours > 0.12 and incoming > 0.12 and c.gdp_growth < 0.015:
                adj.append(Adjustment(
                    code, "tariff", other, -self._deesc, reason="negotiation",
                ))

            # Only ABOVE-BASELINE tariffs hurt exports. Charging the normal
            # ~3% MFN level every tick made exports a one-way ratchet that
            # ground the index to its floor even in a peaceful world.
            export_pressure += max(0.0, incoming - 0.05)

        # Our exports erode with excess tariffs imposed on us.
        exp_delta = _clamp(-3.0 * export_pressure, -2.0, 0.5)
        if abs(exp_delta) > 1e-9:
            adj.append(Adjustment(code, "fiscal", "export_index", exp_delta,
                                  reason="export_pressure"))

        # Supply-chain diversion: capture trade from third-party tariff wars
        # between pairs we are NOT party to, weighted by our trust with both.
        diversion = 0.0
        for a in others:
            for b in others:
                if a >= b:
                    continue
                war = min(world.tariff(a, b), world.tariff(b, a))
                if war > 0.08:
                    proximity = (world.alliance(code, a) + world.alliance(code, b)) / 2.0
                    diversion += war * proximity
        if diversion > 0.02:
            gain = _clamp(self._diversion * diversion, 0.0, 3.0)
            adj.append(Adjustment(
                code, "fiscal", "export_index", gain, reason="supply_chain_diversion",
            ))

        # Trade balance tracks net export/import index drift.
        tb_delta = _clamp(0.0005 * (c.fiscal.export_index - c.fiscal.import_index), -0.01, 0.01)
        adj.append(Adjustment(code, "fiscal", "trade_balance_pct", tb_delta,
                              reason="trade_balance"))
        return adj


# ===========================================================================
#  Sentiment -- confidence, stability respond to macro stress
# ===========================================================================
class SentimentModel(ReactionModel):
    name = "sentiment"

    def react(self, code: str, world: WorldState) -> list[Adjustment]:
        c = world.countries[code]
        g = c.geopolitical
        stress = (
            max(0.0, c.monetary.inflation_cpi - 0.03) * 4.0
            + max(0.0, c.labor.unemployment - 0.05) * 4.0
            + max(0.0, -c.gdp_growth) * 5.0
        )
        target_conf = _clamp(0.7 - stress, 0.1, 0.95)
        conf_delta = _clamp((target_conf - g.consumer_confidence) * 0.1, -0.03, 0.03)

        adj = [
            Adjustment(code, "geopolitical", "consumer_confidence", conf_delta,
                       reason="sentiment"),
            Adjustment(code, "geopolitical", "business_confidence", conf_delta * 0.9,
                       reason="sentiment"),
        ]
        # Severe stress erodes political stability and can spark unrest events.
        if stress > 0.25:
            adj.append(Adjustment(code, "geopolitical", "political_stability",
                                  -0.01 * stress, reason="stress"))
            adj.append(Adjustment(code, "geopolitical", "social_unrest_index",
                                  0.01 * stress, reason="stress"))
            if stress > 0.5 and g.social_unrest_index > 0.45:
                # Numeric pressure only — no canned "sees rising social unrest" headline.
                adj.append(Adjustment(
                    code, "geopolitical", "social_unrest_index", 0.02 * stress,
                    reason="unrest_signal",
                ))
        return adj


# ===========================================================================
#  H200 swap stub -- same interface, NN/LLM-backed later
# ===========================================================================
class NeuralReactionModel(ReactionModel):
    """H200 ``.pt`` ReactionNet: maps country macro vector → 3 market signals,
    then projects those signals into :class:`Adjustment` deltas.
    """

    name = "neural"

    def __init__(
        self,
        endpoint: str | None = None,
        checkpoint: str | Path | None = None,
        *,
        scale: float = 0.35,
    ) -> None:
        self.endpoint = endpoint
        self._path = Path(checkpoint) if checkpoint else None
        self._blob: dict[str, Any] | None = None
        self._module: Any = None
        self._loaded = False
        self._scale = max(0.05, float(scale))
        if self._path is not None:
            self._try_load()

    def _try_load(self) -> None:
        assert self._path is not None
        from ai.agents.checkpoint_formats import CheckpointKind, classify_checkpoint

        kind = classify_checkpoint(self._path)
        if kind is CheckpointKind.SB3_ZIP:
            logger.warning(
                "[neural] SB3 .zip at %s is for trading desks, not world_neural — skipped",
                self._path,
            )
            return
        if kind is not CheckpointKind.TORCH_PT:
            logger.warning("[neural] no usable .pt at %s (%s)", self._path, kind.value)
            return
        try:
            import torch
            import torch.nn as nn

            try:
                blob = torch.load(str(self._path), map_location="cpu", weights_only=False)
            except TypeError:
                blob = torch.load(str(self._path), map_location="cpu")
            if not isinstance(blob, dict) or "state_dict" not in blob:
                logger.warning("[neural] unexpected checkpoint schema at %s", self._path)
                return
            in_dim = int(blob.get("in_dim") or 0)
            out_dim = int(blob.get("out_dim") or 3)
            if in_dim <= 0:
                logger.warning("[neural] in_dim missing in %s", self._path)
                return

            class ReactionNet(nn.Module):
                def __init__(self) -> None:
                    super().__init__()
                    self.net = nn.Sequential(
                        nn.Linear(in_dim, 128),
                        nn.ReLU(),
                        nn.Linear(128, 64),
                        nn.ReLU(),
                        nn.Linear(64, out_dim),
                        nn.Tanh(),
                    )

                def forward(self, x):  # noqa: ANN001
                    return self.net(x)

            module = ReactionNet()
            module.load_state_dict(blob["state_dict"])
            module.eval()
            self._module = module
            self._blob = blob
            self._loaded = True
            logger.info("[neural] loaded ReactionNet policy head <- %s", self._path)
        except ImportError:
            logger.warning(
                "[neural] torch not installed (optional INSTALL_ML) — .pt offline"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[neural] failed to load .pt (%s)", exc)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def _predict(self, vector: list[float]) -> tuple[float, float, float] | None:
        if not self._loaded or self._module is None or self._blob is None:
            return None
        try:
            import torch
            import numpy as np

            x = np.asarray(vector, dtype="float64")
            mean = np.asarray(self._blob.get("x_mean") or [], dtype="float64")
            std = np.asarray(self._blob.get("x_std") or [], dtype="float64")
            if mean.size == x.size and std.size == x.size:
                std = np.where(std < 1e-8, 1.0, std)
                x = (x - mean) / std
            with torch.no_grad():
                t = torch.tensor(x, dtype=torch.float32).unsqueeze(0)
                y = self._module(t).squeeze(0).cpu().numpy()
            vol = float(np.clip((float(y[0]) + 1.0) * 0.5, 0.0, 1.0))  # tanh→[0,1]
            bias = float(np.clip(y[1], -1.0, 1.0))
            prem = float(np.clip(y[2], -1.0, 1.0))
            return vol, bias, prem
        except Exception as exc:  # noqa: BLE001
            logger.debug("[neural] predict failed (%s)", exc)
            return None

    def react(self, code: str, world: WorldState) -> list[Adjustment]:
        c = world.countries.get(code)
        if c is None or not self.is_loaded:
            return []
        try:
            vec = c.to_vector()
        except Exception:  # noqa: BLE001
            return []
        pred = self._predict(list(vec))
        if pred is None:
            return []
        vol, bias, prem = pred
        s = self._scale
        adj: list[Adjustment] = []
        # Volatility → mild CPI / unrest pressure (bounded deltas).
        if vol > 0.15:
            adj.append(
                Adjustment(
                    code,
                    "monetary",
                    "inflation_cpi",
                    0.0008 * vol * s,
                    reason="neural_vol_pressure",
                )
            )
            adj.append(
                Adjustment(
                    code,
                    "geopolitical",
                    "social_unrest_index",
                    0.01 * vol * s,
                    reason="neural_vol_unrest",
                )
            )
        # Directional bias → growth nudge + opposing policy rate lean.
        if abs(bias) > 0.05:
            adj.append(
                Adjustment(
                    code,
                    "",
                    "gdp_growth",
                    0.002 * bias * s,
                    reason="neural_bias_growth",
                )
            )
            adj.append(
                Adjustment(
                    code,
                    "monetary",
                    "interest_rate",
                    -0.0005 * bias * s,
                    reason="neural_bias_rate",
                )
            )
        # Risk premium → yield / debt stress.
        if abs(prem) > 0.05:
            adj.append(
                Adjustment(
                    code,
                    "monetary",
                    "yield_10y",
                    0.001 * prem * s,
                    reason="neural_risk_premium",
                )
            )
        return adj


def default_models() -> list[ReactionModel]:
    """Default heuristic agent stack (+ neural when a mapped head is loaded)."""
    stack: list[ReactionModel] = [
        CentralBankModel(),
        TradeMinistryModel(),
        SentimentModel(),
    ]
    try:
        from ai.agents.agent_loaders import resolve_world_neural_checkpoint

        path = resolve_world_neural_checkpoint()
        if path is not None:
            neural = NeuralReactionModel(checkpoint=path)
            if neural.is_loaded:
                stack.append(neural)
                logger.info("[neural] live policy head armed <- %s", path)
    except Exception as exc:  # noqa: BLE001 - never block boot on optional neural
        logger.warning("world_neural optional load skipped (%s)", exc)
    return stack
