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

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ai.simulator_engine.macro_vectors import WorldState


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
            policy_event: str | None = None
            policy_level = "info"
            if abs(infl_gap) > 0.02 and abs(rate_delta) >= self._step - 1e-6:
                direction = "hikes" if rate_delta > 0 else "cuts"
                policy_event = (
                    f"{c.name} central bank {direction} rates "
                    f"to fight {m.inflation_cpi*100:.1f}% inflation"
                )
                policy_level = "warn" if rate_delta > 0 else "ok"
            adj.append(
                Adjustment(
                    code,
                    "monetary",
                    "interest_rate",
                    rate_delta,
                    reason="taylor_rule",
                    event=policy_event,
                    event_level=policy_level,
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
                        event=(f"{c.name} imposes {(ours + retal)*100:.0f}% retaliatory "
                               f"tariffs on {world.countries[other].name} imports"),
                        event_level="danger",
                    ))
            # De-escalation: if both sides high and we're hurting, negotiate down.
            elif ours > 0.12 and incoming > 0.12 and c.gdp_growth < 0.015:
                adj.append(Adjustment(
                    code, "tariff", other, -self._deesc, reason="negotiation",
                    event=(f"{c.name} and {world.countries[other].name} open trade "
                           f"talks; tariffs ease toward {(ours - self._deesc)*100:.0f}%"),
                    event_level="ok",
                ))

            export_pressure += incoming  # higher foreign tariffs hurt our exports

        # Our exports erode with tariffs imposed on us.
        exp_delta = _clamp(-3.0 * export_pressure, -2.0, 0.5)
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
                event=(f"{c.name} captures diverted supply chains "
                       f"(+{gain:.1f} export index)") if gain > 0.8 else None,
                event_level="ok",
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
                adj.append(Adjustment(
                    code, "geopolitical", "social_unrest_index", 0.0,
                    reason="unrest_signal",
                    event=f"{c.name} sees rising social unrest amid economic stress",
                    event_level="danger",
                ))
        return adj


# ===========================================================================
#  H200 swap stub -- same interface, NN/LLM-backed later
# ===========================================================================
class NeuralReactionModel(ReactionModel):
    """Placeholder for a trained policy (PPO / LLM agent) served from H200.

    Inject this in place of the heuristic models once a checkpoint exists:
        WorldKernel(models=[NeuralReactionModel(endpoint="...")])
    Until a model is wired, it proposes nothing (no-op) so the system stays safe.
    """

    name = "neural"

    def __init__(self, endpoint: str | None = None) -> None:
        self.endpoint = endpoint

    def react(self, code: str, world: WorldState) -> list[Adjustment]:  # noqa: ARG002
        # TODO(H200): batch country feature vectors -> policy server -> deltas.
        return []


def default_models() -> list[ReactionModel]:
    """Default heuristic agent stack (the DI seam)."""
    return [CentralBankModel(), TradeMinistryModel(), SentimentModel()]
